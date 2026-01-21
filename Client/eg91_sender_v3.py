import time
import ujson
import _thread
from machine import UART, Pin
import esp32
import re

#TODO: save ip addr and send via log
class Eg91SenderV3():
    """
        Improved Sender class to control Quectel EG915N radio with interrupt-based UART communication.

        Provide methods to publish on MQTT broker, 
        access and manage local filesystem and perform HTTPS GET/POST requests.

    """

    MAX_RESP_TIME = {
        # SIM/APN commands
        "AT+CPIN": 5000,
        "AT+QICSGP": 2000,
        "AT+QIACT": 150000,  # Increased timeout for network activation
        "AT+QIDEACT": 40000,

        # MQTT commands
        "AT+QMTCFG": 5000,
        "AT+QSSLCFG": 5000,
        "AT+QMTOPEN": 10000,
        "AT+QMTCONN": 10000,
        "AT+QMTSUB": 5000,
        "AT+QMTUNS": 5000,
        "AT+QMTDISC": 10000,
        "AT+QMTCLOSE": 5000,
        "AT+QMTRECV": 2000,
        "AT+QMTPUBEX": 10000,

        # TIME commands
        "AT+QLTS": 5000,

        # FS commands
        "AT+QFUPL": 30000,
        "AT+QFLST": 10000,
        "AT+QFOPEN": 5000,
        "AT+QFREAD": 10000,
        "AT+QFCLOSE": 2000,
        "AT+QFDEL": 5000,

        # HTTPS commands
        "AT+QHTTPCFG": 2000,
        "AT+CFUN": 10000,
        "AT+QHTTPURL": 5000,
        "AT+QHTTPGET": 80000,
        "AT+QHTTPREAD": 10000,
        "AT+QHTTPPOST": 80000,
        "AT+QHTTPSTOP": 5000
    }

    # MQTT and HTTP(S) context
    PDP_CTXID = 1       #TODO: Potresti leggerlo dal response del comando AT+QACT?
    MQTT_SSL_CTXID = 1   #2
    HTTPS_SSL_CTXID = 2

    # HTTP supported content types
    HTTP_CONTENT_TYPES = {
        "application/x-www-form-urlencoded": 0,
        "text/plain": 1,
        "application/octet-stream": 2,
        "multipart/form-data": 3,
        "application/json": 4,
        "image/jpeg": 5
    }

    # Connection states
    STATE_DISCONNECTED = 0
    STATE_CONNECTING = 1
    STATE_CONNECTED = 2
    STATE_ERROR = 3

    ENABLED=False

    def __init__(self, adapter, config: dict):
        try:

            if config["adapter"] != "uart" or not isinstance(adapter, UART):
                self.log_error("invalid adapter type, must be uart")
                raise ValueError("Invalid adapter type, must be uart")
            else:
                self._uart = adapter

            self._apn = config["apn"]
            self._endpoint = config["endpoint"]
            self._port = config["port"]
            self._client_id = config["client_id"]
            self._pub_topic = config["pub_topic"]
            self._sub_topic = config["sub_topic"]
            self._auth_type = config["auth_type"]

            if self._auth_type == "passwd":
                self._mqtt_user = config["mqtt_user"]
                self._mqtt_pass = config["mqtt_pass"]
            elif self._auth_type == "certs":
                self._ca = config["ca"]
                self._cert = config["cert"]
                self._key = config["key"]
            else:
                raise KeyError("auth_type")

            # Connection state management
            self._connection_state = self.STATE_DISCONNECTED
            self._mqtt_connected = False
            self._network_registered = False
            self._received_mqtt_data = {}
            self._pdp_context_active = False

            # Initialize interrupt-based communication
            self._init_uart_interrupt()
        except KeyError as key:
            raise ValueError(f"Invalid config value: {key}")

    def _init_uart_interrupt(self):
        """Initialize interrupt-based UART communication"""
        self._rx_buffer = ""
        self._response_ready = False
        self._command_pending = False
        self._wait_for_event = None
        self._response_lock = _thread.allocate_lock()
        self._unsolicited_messages = []

        # Set up UART interrupt for incoming data
        self._uart.irq(trigger=UART.IRQ_RX, handler=self._uart_irq_handler)
        self.log_debug("UART interrupt initialized")

    def _uart_irq_handler(self, uart_obj):
        """UART interrupt handler - called when data is received"""
        
        try:
            if uart_obj.any():

                data = uart_obj.read(10000)

                self.log_info("(_uart_irq_handler) Data received!")
                self.log_info(f"(_uart_irq_handler) {len(data)} bytes read from UART")
                self.log_debug(f"(_uart_irq_handler) Raw data: {data}")
                
                if data:
                    self.log_info("(_uart_irq_handler) waiting for lock")
                    with self._response_lock:
                        self.log_info("(_uart_irq_handler) lock acquired")
                        self._rx_buffer += data.decode('utf-8')
                        #self._rx_buffer += data.decode('utf-8', 'ignore')
                        #self.log_info(f"(_uart_irq_handler) rx_buffer: {self._rx_buffer.strip()}")

                        # Check for MQTT messages
                        if "+QMTRECV:" in self._rx_buffer:
                            self._handle_mqtt_message()

                        # Check for connection status changes
                        if "+QMTSTAT:" in self._rx_buffer:
                            self._handle_mqtt_status()

                        # If we're waiting for a specific event, check for it
                        if self._command_pending and self._wait_for_event:
                            if self._wait_for_event in self._rx_buffer:
                                self._response_ready = True
                        # Otherwise, check for standard command responses
                        elif self._command_pending and any(term in self._rx_buffer for term in ["OK", "ERROR", "+CME ERROR", "+CMS ERROR"]):
                            self._response_ready = True
                    self.log_info("(_uart_irq_handler) lock released")
            #else:
                #self.log_debug("(_uart_irq_handler) No data available in UART")
        except Exception as e:
            self.log_error(f"UART IRQ handler error: {e}")

    def _handle_mqtt_message(self):
        """Handle incoming MQTT messages"""
        try:
            lines = self._rx_buffer.split('\n')
            for line in lines:
                if '+QMTRECV:' in line:
                    incoming_data = self.decode_mqtt_data(line)
                    if incoming_data :
                        self._received_mqtt_data = incoming_data
                    self.log_debug(
                        f"Received MQTT data: {self._received_mqtt_data}")
        except Exception as e:
            self.log_error(f"Error handling MQTT message: {e}")

    def _handle_mqtt_status(self):
        """Handle MQTT status changes"""
        try:
            lines = self._rx_buffer.split('\n')
            for line in lines:
                if '+QMTSTAT:' in line:
                    # Parse status: +QMTSTAT: <client_idx>,<error_code>
                    parts = line.replace('+QMTSTAT:', '').strip().split(',')
                    if len(parts) >= 2:
                        error_code = int(parts[1])
                        if error_code != 5: #5 = NO ERROR
                            self.log_error(
                                f"MQTT connection error: {error_code}")
                            self._mqtt_connected = False
                            self._connection_state = self.STATE_ERROR
        except Exception as e:
            self.log_error(f"Error handling MQTT status: {e}")

    def _send_command_interrupt(self, command, wait_time=5000, wait_for_event=None) -> tuple[str, bool]:
        """
        Send AT command using interrupt-based communication with improved error handling

        :param str command: AT command to send
        :param int wait_time: maximum wait time in milliseconds
        :param str wait_for_event: specific event to wait for
        :return tuple: (response, success)
        """
        self.log_debug(f"Sending AT command: {command}")

        # Get timeout from command table if available
        if wait_time == 5000:  # Default value
            cmd_key = command.split('=')[0].split('?')[0]
            wait_time = self.MAX_RESP_TIME.get(cmd_key, 5000)

        self.log_info("(_send_command_interrupt) waiting for lock 1")
        with self._response_lock:
            self.log_info("(_send_command_interrupt) lock 1 acquired")
            self._rx_buffer = ""
            self._response_ready = False
            self._command_pending = True
            self._wait_for_event = wait_for_event
        self.log_info("(_send_command_interrupt) lock 1 released")

        # Send command
        self._uart.write(command + "\r")
        self._uart.flush()

        #TODO: da ottimizzare?
        
        # Wait for response with timeout
        start_time = time.ticks_ms()
        while not self._response_ready:
            if time.ticks_diff(time.ticks_ms(), start_time) > wait_time:
                self._command_pending = False
                self._wait_for_event = None
                raise RuntimeError(
                    f"Timeout waiting for response to command: {command}")
            time.sleep_ms(10)

        # Get response
        self.log_info("(_send_command_interrupt) waiting for lock 2")
        with self._response_lock:
            self.log_info("(_send_command_interrupt) lock 2 acquired")
            response = self._rx_buffer
            self._command_pending = False
            self._response_ready = False
            self._wait_for_event = None
        self.log_info("(_send_command_interrupt) lock 2 released")

        self.log_debug(f"Received response: {response}")

        # Determine success
        if wait_for_event:
            success = wait_for_event in response
        else:
            success = "OK" in response and "ERROR" not in response

        return response, success

    def _send_command(self, command, wait_time=2000, max_attempts=1) -> str:
        """
        Send AT command with fallback to old method if needed
        """
        try:
            response, success = self._send_command_interrupt(
                command, wait_time)
            if not success:
                raise RuntimeError(f"Command failed: {command}")
            return response
        except Exception as e:
            self.log_error(f"Command failed: {e}")
            # Fallback to old blocking method for critical commands
            return self._send_command_blocking(command, wait_time, max_attempts)

    def _send_command_blocking(self, command, wait_time=2000, max_attempts=5) -> str:
        """
        Fallback blocking command method (original implementation)
        """
        self.log_debug(f"Sending AT command (blocking): {command}")
        self._uart.write(command + "\r")
        attempts = 0

        while attempts < max_attempts:
            time.sleep_ms(wait_time)
            response = self._uart.read(10000)

            if response:
                decoded_response = response.decode('utf-8')
                self.log_debug(
                    f"UART answer on attempt {attempts}: {decoded_response}")
                return decoded_response
            else:
                attempts += 1
                self.log_debug(f"No response from UART on attempt {attempts}")

        raise RuntimeError(
            f"Error while sending AT command, no response received. Sent command: {command}")

    def decode_mqtt_data(self, msg: str) -> dict:
        """Decode MQTT message with improved error handling"""
        try:
            # Extract JSON from QMTRECV message
            if '+QMTRECV:' in msg:
                # Format: +QMTRECV: 0,0,"topic","data"
                parts = msg.split('"')
                if len(parts) >= 4:
                    json_data = parts[3]
                    json_data = json_data.replace("'", '"')
                    return ujson.loads(json_data)
            return {}
        except Exception as e:
            self.log_error(f"Error decoding MQTT data: {e}")
            return {}

    def _check_network_registration(self) -> bool:
        """Check if device is registered to network"""
        try:
            response, success = self._send_command_interrupt("AT+CREG?")
            if success and "+CREG:" in response:
                # Parse: +CREG: <n>,<stat>
                for line in response.split('\n'):
                    if '+CREG:' in line:
                        parts = line.replace('+CREG:', '').strip().split(',')
                        if len(parts) >= 2:
                            status = int(parts[1])
                            # 1=registered home, 5=registered roaming
                            self._network_registered = status in [1, 5]
                            return self._network_registered
            return False
        except Exception as e:
            self.log_error(f"Error checking network registration: {e}")
            return False

    def _recovery_sequence(self) -> bool:
        """Attempt to recover from error state"""
        self.log_info("Attempting recovery sequence")

        try:
            # Reset UART buffers
            self.log_info("(_recovery_sequence) Waiting lock")
            with self._response_lock:
                self.log_info("(_recovery_sequence) Lock acquired")
                self._rx_buffer = ""
                self._response_ready = False
                self._command_pending = False
            self.log_info("(_recovery_sequence) Lock released")

            # Basic AT test
            response, success = self._send_command_interrupt(
                "AT", wait_time=2000)
            if not success:
                self.log_error("Basic AT command failed during recovery")
                return False

            # Check network registration
            if not self._check_network_registration():
                self.log_error("Network not registered during recovery")
                return False

            # Reset connection states
            self._connection_state = self.STATE_DISCONNECTED
            self._mqtt_connected = False

            self.log_info("Recovery sequence completed successfully")
            return True

        except Exception as e:
            self.log_error(f"Recovery sequence failed: {e}")
            return False

    def enable(self):
        """
        Enable sender module with improved error handling and recovery
        """
        if not self.ENABLED:
            self.log_info("Enabling EG91 sender module")
            #TODO: get data diagnostic, AT

            max_retries = 1
            for attempt in range(max_retries):
                try:

                    self._connection_state = self.STATE_CONNECTING

                    # Clear buffers
                    self._uart.read(10000)
                    self.log_info("(enable) waiting for lock")
                    with self._response_lock:
                        self.log_info("(enable) lock acquired")
                        self._rx_buffer = ""
                    self.log_info("(enable) lock released")

                    # Basic configuration with error checking
                    self.log_debug("Starting basic configuration")

                    # Test basic communication
                    response, success = self._send_command_interrupt("AT")
                    if not success:
                        raise RuntimeError("Basic AT command failed")

                    # Disable echo
                    self._send_command_interrupt("ATE0")

                    '''
                    # Configure context
                    self._send_command_interrupt(
                        f'AT+CGDCONT={self.PDP_CTXID},"IP","{self._apn}"')
                    '''

                    # Check SIM status
                    response, success = self._send_command_interrupt("AT+CPIN?")
                    if not success or "READY" not in response:
                        raise RuntimeError("SIM not ready")

                    # Check network registration
                    if not self._check_network_registration():
                        self.log_info("Waiting for network registration...")
                        time.sleep(5)
                        if not self._check_network_registration():
                            raise RuntimeError("Network registration failed")

                    self.log_info("Successfully connected to the cellular network")

                    self._activate_pdp_context()
                    
                    # Initialization and configuration of the MQTT connection

                    #self._initialize_mqtt_connection()

                    self.log_info("EG91 sender module enabled successfully")

                    self.ENABLED=True

                    # Resetto contatore lora 

                    try:
                        nvs = esp32.NVS("storage")
                        nvs.set_i32("lora_retry", 0)
                        nvs.commit()
                    except Exception as e:
                        self.log_error(f"NVS write error for lora_retry in eg91: {e}")
                    return True
                
                except Exception as e:
                    self.log_error(f"Enable attempt {attempt + 1} failed: {e}")
                    
        return False
    
    def _activate_pdp_context(self) -> bool:
        
        # Configure APN
        apn_cmd = f'AT+QICSGP={self.PDP_CTXID},1,"{self._apn}","","",1'
        response, success = self._send_command_interrupt(apn_cmd)
        if not success:
            raise RuntimeError("APN configuration failed")
        
        '''
        response, success = self._send_command_interrupt('AT+CFUN=1,1')
        if not success:
            self.log_error("Failed to set full functionality")
            return None
        '''

        # Verify activation
        response, success = self._send_command_interrupt("AT+QIACT?")
        if not success:
            raise RuntimeError("Context verification failed")

        # Activate context
        response, success = self._send_command_interrupt(
            f"AT+QIACT={self.PDP_CTXID}")
        if not success:
            raise RuntimeError("Context activation failed")

        # Verify activation
        response, success = self._send_command_interrupt("AT+QIACT?")
        if not success or "+QIACT:" not in response:
            raise RuntimeError("Context verification failed")

    def _deactivate_pdp_context(self) -> bool:
        # Deactivate context
        response, success = self._send_command_interrupt(f'AT+QIDEACT={self.PDP_CTXID}', wait_time=40000)

        if not success:
            self.log_info("Failed to deactivate context cleanly")
            raise RuntimeError("Context deactivation failed")      

    def initialize_mqtt_connection(self) -> bool:
        # Open MQTT connection
        if self._open_mqtt_connection():
            # Subscribe to topic
            resp, success = self._send_command_interrupt(
                f"AT+QMTSUB=0,1,{self._sub_topic},0")
            if success:
                # Check for immediate message
                time.sleep_ms(500)
                self.log_info("(_open_mqtt_connection) waiting for lock")
                with self._response_lock:
                    self.log_info("(_open_mqtt_connection) lock acquired")
                    if "+QMTRECV" in self._rx_buffer:
                        self._handle_mqtt_message()
                self.log_info("(_open_mqtt_connection) lock released")

                # Delete any retained messages
                self._delete_retained_msg()

                self._connection_state = self.STATE_CONNECTED
                self._mqtt_connected = True
            else:
                raise RuntimeError("MQTT connection failed")
    
    def _open_mqtt_connection(self) -> bool:
        """
        Open MQTT connection with SSL using preloaded local UFS certificates.
        Improved with better error handling and status checking.
        """
        self.log_info("Starting MQTT connection")

        try:
            if self._auth_type == "certs":
                # Configure MQTT connection with SSL
                self._send_command_interrupt(f'AT+QMTCFG="recv/mode",0,0,1')
                self._send_command_interrupt(
                    f'AT+QMTCFG="SSL",0,1,{self.MQTT_SSL_CTXID}')

                # Configure SSL certificates
                self._send_command_interrupt(
                    f'AT+QSSLCFG="clientcert",{self.MQTT_SSL_CTXID},"UFS:{self._cert}"')
                self._send_command_interrupt(
                    f'AT+QSSLCFG="clientkey",{self.MQTT_SSL_CTXID},"UFS:{self._key}"')
                self._send_command_interrupt(
                    f'AT+QSSLCFG="cacert",{self.MQTT_SSL_CTXID},"UFS:{self._ca}"')

                # Configure SSL parameters
                self._send_command_interrupt(
                    f'AT+QSSLCFG="seclevel",{self.MQTT_SSL_CTXID},2')
                self._send_command_interrupt(
                    f'AT+QSSLCFG="sslversion",{self.MQTT_SSL_CTXID},4')
                self._send_command_interrupt(
                    f'AT+QSSLCFG="ciphersuite",{self.MQTT_SSL_CTXID}, "0xFFFF"')
                self._send_command_interrupt(
                    f'AT+QSSLCFG="ignorelocaltime",{self.MQTT_SSL_CTXID},1')

            # Open MQTT connection
            response, success = self._send_command_interrupt(
                f'AT+QMTOPEN=0,"{self._endpoint}",{self._port}',
                wait_for_event="+QMTOPEN:"
            )

            if not success:
                self.log_error(f"Error opening MQTT connection: {response}")
                return False

            # Connect with credentials
            if self._auth_type == "passwd":
                connect_cmd = f'AT+QMTCONN=0,"{self._client_id}","{self._mqtt_user}","{self._mqtt_pass}"'
            else:
                connect_cmd = f'AT+QMTCONN=0,"{self._client_id}"'

            connect_response, success = self._send_command_interrupt(
                connect_cmd,
                wait_for_event="+QMTCONN:"
            )

            if success:
                self.log_info("MQTT connection successfully established")
                return True
            else:
                self.log_error(
                    f"Error connecting to MQTT broker: {connect_response}")
                return False

        except Exception as e:
            self.log_error(f"MQTT connection failed with exception: {e}")
            return False

    def send_mqtt_data(self, message: str) -> bool:
        """
        Send message with improved error handling and connection recovery
        """
        if self._connection_state != self.STATE_CONNECTED:
            self.log_error("Cannot send data: not connected")
            return False

        try:
            self.log_info(
                f"Publishing on topic {self._pub_topic} message: {message}")

            # Check connection status first
            if not self._mqtt_connected:
                self.log_info("MQTT not connected, attempting reconnection")
                if not self.enable():
                    return False

            response, success = self._send_command_interrupt(
                f'AT+QMTPUBEX=0,0,0,1,"{self._pub_topic}",{len(message)}',
                wait_for_event=">"
            )

            if success and (">" in response or "+QMTPUB:" in response):
                response, success = self._send_command_interrupt(
                    message+ "\x1A",
                    wait_for_event="+QMTPUBEX",
                    wait_time=10000
                )
                self.log_info("Message has been successfully sent.")
                return True
            else:
                self.log_error(f"Error publishing MQTT message: {response}")
                # Try to recover connection
                self._mqtt_connected = False
                return False

        except Exception as e:
            self.log_error(f"Send data error: {e}")
            self._mqtt_connected = False
            return False

    def get_mqtt_data(self):
        """Get received data with thread safety"""
        self.log_info("(_get_data) waiting for lock")
        with self._response_lock:
            self.log_info("(_get_data) lock acquired")
            data = self._received_mqtt_data.copy() if self._received_mqtt_data else None
        self.log_info("(_get_data) lock released")
        return data

    def close_mqtt_connection(self):
        """
        Close mqtt connection with improved cleanup
        """
        try:
            self.log_info("Closing MQTT connection")

            # Unsubscribe
            if self._mqtt_connected:
                response, success = self._send_command_interrupt(
                    f"AT+QMTUNS=0,1,{self._sub_topic}")
                if not success:
                    self.log_error(
                        f"Unable to unsubscribe topic {self._sub_topic}")

            # Disconnect and close
            self._send_command_interrupt("AT+QMTDISC=0")
            #time.sleep(5)
            #resp, _ = self._send_command_interrupt("AT+QMTCLOSE=?")
            #self.log_info(resp)
            #response, success = self._send_command_interrupt("AT+QMTCLOSE=0")

            # Clean up state
            self._mqtt_connected = False
            self._connection_state = self.STATE_DISCONNECTED

            return success

        except Exception as e:
            self.log_error(f"Error closing MQTT connection: {e}")
            return False
    
    def disable(self):
        try:
            if self.ENABLED:
                self.log_info("Disabling EG91 sender module")

                #self.close_mqtt_connection()
                
                # Deactivate context
                self._deactivate_pdp_context()

                # Disable UART interrupt
                self._uart.irq(trigger=0, handler=None)

                self.ENABLED=False

                self.log_info("EG91 sender module disabled successfully")
            else:
                self.log_info("EG91 sender module already disabled")
        except Exception as e:
            self.log_error(f"Error disabling EG91 sender module: {e}")
            self.ENABLED=False

    def is_powered_on(self):
        """Check if radio is powered on with timeout"""
        try:
            self.log_info("checking radio status")
            response, success = self._send_command_interrupt(
                "AT", wait_time=2000)
            return success
        except Exception as e:
            print(e)
            return False

    def get_mqtt_connection_status(self):
        """Get detailed connection status"""
        return {
            "state": self._connection_state,
            "mqtt_connected": self._mqtt_connected,
            "network_registered": self._network_registered
        }

    # Keep all existing methods (get_time, upload_certs, UFS methods, HTTPS methods, etc.)
    # but update them to use the new interrupt-based communication

    def get_time(self):
        """
        Query the current GMT time with improved error handling
        """
        try:
            resp, success = self._send_command_interrupt("AT+QLTS=1", wait_time=10000)
            if not success:
                raise RuntimeError("Time command failed")

            lines = resp.splitlines()
            for line in lines:
                print(line)
                if "+QLTS" in line:
                    time_str = line.replace(
                        '+QLTS: "', "").replace('"', "").replace("OK", "").strip()
                    date, time_part, _ = time_str.split(',')
                    year, month, day = date.split("/")
                    hour, min, sec = time_part.split(":")
                    sec = sec.split("+")[0]
                    return f"{year}-{month}-{day} {hour}:{min}:{sec}"

            raise RuntimeError("Unable to parse time")

        except Exception as e:
            self.log_error(f"Get time error: {e}")
            return None

    def get_time_ms(self):
        """
        Query the current GMT time via AT+QLTS and return timestamp in milliseconds
        """
        try:
            resp, success = self._send_command_interrupt("AT+QLTS=1", wait_time=10000)
            if not success:
                raise RuntimeError("Time command failed")

            lines = resp.splitlines()
            for line in lines:
                line = line.strip()
                if line.startswith("+QLTS"):
                    # Estrapola la stringa tra le virgolette
                    time_str = line.split('"')[1]  # "2024/01/19,14:42:30+00"

                    # Rimuove timezone
                    dt_part = time_str.split('+')[0]  # "2024/01/19,14:42:30"
                    date_str, time_str_part = dt_part.split(',')

                    # Converte in timestamp ms
                    year, month, day = map(int, date_str.split('/'))
                    hour, minute, second = map(int, time_str_part.split(':'))
                    tm = (year, month, day, hour, minute, second, 0, 0)
                    return int(time.mktime(tm) * 1000)

            raise RuntimeError("Unable to parse time")

        except Exception as e:
            self.log_error(f"Get time error: {e}")
            return None

    def _delete_retained_msg(self) -> bool:
        """Delete retained messages with improved error handling"""
        try:
            response, success = self._send_command_interrupt(
                f'AT+QMTPUBEX=0,0,0,1,"{self._sub_topic}",2',
                wait_for_event=">"
            )

            if success and (">" in response or "+QMTPUB:" in response):
                response, success = self._send_command_interrupt(
                    "{}"+ "\x1A",
                    wait_for_event="+QMTPUBEX",
                    wait_time=10000
                )
                self.log_info("Retained message deleted successfully.")
                return True
            else:
                self.log_error(f"Error deleting retained message: {response}")
                return False

        except Exception as e:
            self.log_error(f"Delete retained message error: {e}")
            return False

    def upload_to_ufs(self, name: str, content: str) -> bool:
        """
        Upload a file to the EG91 FS with improved error handling
        """
        try:
            self.log_info(f"Starting upload of file {name} to UFS.")
            content_length = len(content)

            command = f'AT+QFUPL="UFS:{name}",{content_length},10'
            response, success = self._send_command_interrupt(
                command, wait_time=10000)

            if success and "CONNECT" in response:
                self.log_debug("Sending file content")
                self._uart.write(content.encode('utf-8'))
                self._uart.flush()

                # Wait for completion
                time.sleep(2)
                response = self._uart.read(10000)
                if response and b"OK" in response:
                    self.log_info(f"File {name} uploaded successfully to UFS.")
                    return True
                else:
                    self.log_error(f"Error uploading file {name}: {response}")
                    return False
            else:
                self.log_error(
                    f"EG91 not ready to receive file {name}: {response}")
                return False

        except Exception as e:
            self.log_error(f"Upload to UFS error: {e}")
            return False

    def upload_certs(self, config: dict) -> bool:
        """
        Upload all certificates to UFS with improved error handling
        """
        base_dir = config["certs_dir"]

        try:
            with open(f"{base_dir}/{self._ca}", "r") as f:
                ca_cert = f.read()
            with open(f"{base_dir}/{self._cert}", "r") as f:
                device_cert = f.read()
            with open(f"{base_dir}/{self._key}", "r") as f:
                private_key = f.read()

            # Upload certificates with error checking
            results = [
                self.upload_to_ufs(self._ca, ca_cert),
                self.upload_to_ufs(self._cert, device_cert),
                self.upload_to_ufs(self._key, private_key)
            ]

            success = all(results)
            if success:
                self.log_info("All certificates uploaded successfully")
            else:
                self.log_error("Some certificates failed to upload")

            return success

        except Exception as e:
            self.log_error(f"Error uploading certificates: {e}")
            return False

    def https_get_request(self, url: str, timeout=80) -> str:
        """
        Send HTTPS GET request, by setting a new connection to apn in a different context from mqtt connection

        :param str url: url string with format "https://xxx.xxx.xxx/file"
        :param int timeout: timeout to get and read HTTPS response in seconds
        :return str | None: string containing http response or None if any error occurs
        """
        try:
            self.log_info(f"Starting HTTPS GET request to: {url}")
            
            # Configure the PDP context ID
            response, success = self._send_command_interrupt(f'AT+QHTTPCFG="contextid",{self.PDP_CTXID}')
            if not success:
                self.log_error("Failed to configure HTTP context ID")
                return None
                
            # Allow to output HTTPS response header
            response, success = self._send_command_interrupt('AT+QHTTPCFG="responseheader",1')
            if not success:
                self.log_error("Failed to configure response header")
                return None

            # Set ssl context id
            response, success = self._send_command_interrupt(f'AT+QHTTPCFG="sslctxid",{self.HTTPS_SSL_CTXID}')
            if not success:
                self.log_error("Failed to configure SSL context ID")
                return None    
                
            # Set SSL cipher suite as 0xFFFF which means ALL
            response, success = self._send_command_interrupt(f'AT+QSSLCFG="ciphersuite",{self.HTTPS_SSL_CTXID},0xFFFF')
            if not success:
                self.log_error("Failed to configure SSL cipher suite")
                return None
                
            # Set SSL verify level as 0 which means CA certificate is not needed
            response, success = self._send_command_interrupt(f'AT+QSSLCFG="seclevel",{self.HTTPS_SSL_CTXID},0')
            if not success:
                self.log_error("Failed to configure SSL security level")
                return None

            # Set the URL which will be accessed
            response, success = self._send_command_interrupt(
                f'AT+QHTTPURL={len(url)},{timeout}',
                wait_for_event="CONNECT"
            )
            if not success or "CONNECT" not in response:
                self.log_error("Failed to set HTTPS URL")
                return None
                
            response, success = self._send_command_interrupt(url)
            if not success:
                self.log_error("Failed to send URL")
                return None

            # Send HTTPS GET request and the maximum response time is 80s.
            response, success = self._send_command_interrupt(
                f'AT+QHTTPGET={timeout}',
                wait_time=timeout * 1000 + 10000  # Convert to ms and add buffer
            )
            
            match = re.search(r"\+QHTTPGET: \d+,(\d+)", response)
            if not success or not match:
                self.log_error(f"HTTPS GET request failed: {response}")
                self.close_https_connection()
                return None

            http_status = int(match.group(1))
            if http_status <200 or http_status >=300:
                self.log_error(f"HTTPS GET request failed (STATUS CODE: {http_status}): {response}")
                self.close_https_connection()
                return None

            # Read HTTPS response information and output it via UART.
            response, success = self._send_command_interrupt(
                f'AT+QHTTPREAD={timeout}',
                wait_time=timeout * 500  # Half timeout in ms
            )
            
            # Cleanup connection
            self.close_https_connection()

            if success and response:
                # Clean up the response
                cleaned_response = response.replace("OK", "").replace("+QHTTPREAD: 0", "").strip()
                self.log_info("HTTPS GET request completed successfully")
                return cleaned_response
            else:
                self.log_error("Failed to read HTTPS response")
                return None

        except Exception as e:
            self.log_error(f"HTTPS GET request error: {e}")
            self.close_https_connection()
            return None

    def https_post_request(self, url: str, body, timeout=80, content_type="text/plain") -> str:
        """
        Send HTTPS POST request, by setting a new connection to apn in a different context from mqtt connection
        To succeed mqtt connection must be closed

        :param str url: url string with format https://xxx.xxx.xxx/file
        :param body: POST request body
        :param int timeout: timeout to get and read HTTPS response in seconds
        :param str content_type: supported content types are: 
                application/x-www-form-urlencoded,
                text/plain, 
                application/octet-stream, 
                multipart/form-data,
                application/json,
                image/jpeg
        :return str | None: string containing http response or None if any error occurs
        """
        try:

            self.log_info(f"Starting HTTPS POST request to: {url}")

            if content_type not in self.HTTP_CONTENT_TYPES:
                self.log_error(f"Unsupported content type: {content_type}")
                return None
            
            # Configure the PDP context ID
            response, success = self._send_command_interrupt(f'AT+QHTTPCFG="contextid",{self.PDP_CTXID}')
            if not success:
                self.log_error("Failed to configure HTTP context ID")
                return None
  
            
            # Set ssl context id
            response, success = self._send_command_interrupt(f'AT+QHTTPCFG="sslctxid",{self.HTTPS_SSL_CTXID}')
            if not success:
                self.log_error("Failed to configure SSL context ID")
                return None

            # Set content type
            response, success = self._send_command_interrupt(
                f'AT+QHTTPCFG="contenttype",{self.HTTP_CONTENT_TYPES[content_type]}'
            )
            if not success:
                self.log_error("Failed to configure content type")
                return None
                
            # Set SSL cipher suite as 0xFFFF which means ALL
            response, success = self._send_command_interrupt(f'AT+QSSLCFG="ciphersuite",{self.HTTPS_SSL_CTXID},0xFFFF')
            if not success:
                self.log_error("Failed to configure SSL cipher suite")
                return None
                
            # Set SSL verify level as 0 which means CA certificate is not needed
            response, success = self._send_command_interrupt(f'AT+QSSLCFG="seclevel",{self.HTTPS_SSL_CTXID},0')
            if not success:
                self.log_error("Failed to configure SSL security level")
                return None

            # Set the URL which will be accessed
            response, success = self._send_command_interrupt(
                f'AT+QHTTPURL={len(url)},{timeout}',
                wait_for_event="CONNECT"
            )
            if not success or "CONNECT" not in response:
                self.log_error("Failed to set HTTPS URL")
                return None
                
            response, success = self._send_command_interrupt(url)
            if not success:
                self.log_error("Failed to send URL")
                return None 

            # Send HTTPS POST request
            response, success = self._send_command_interrupt(
                f'AT+QHTTPPOST={len(body)},{timeout},{timeout}',
                wait_time=10000,
                wait_for_event="CONNECT"
            )
            
            if not success or "CONNECT" not in response:
                self.log_error(f"Failed to initiate HTTPS POST: {response}")
                self.close_https_connection()
                return None

            # Send body data in chunks
            self.log_debug("Sending POST body data")
            chunk_size = 128
            body_bytes = body.encode('utf-8') if isinstance(body, str) else body
            
            for i in range(0, len(body_bytes), chunk_size):
                chunk = body_bytes[i:i+chunk_size]
                self._uart.write(chunk)
                self._uart.flush()
                time.sleep_ms(10)  # Small delay between chunks

            # Wait for POST completion and read response
            time.sleep(2)  # Allow time for POST to complete

            
            response, success = self._send_command_interrupt(
                f'AT+QHTTPREAD={timeout}',
                wait_time=timeout * 500  # Half timeout in ms
            )
            
            # Cleanup connection
            self.close_https_connection()
            
            if success and response:
                # Clean up the response
                cleaned_response = response.replace("OK", "").replace("+QHTTPREAD: 0", "").strip()
                self.log_info("HTTPS POST request completed successfully")
                return cleaned_response
            else:
                self.log_error("Failed to read HTTPS POST response")
                return None

        except Exception as e:
            self.log_error(f"HTTPS POST request error: {e}")
            self.close_https_connection()
            return None

    def close_https_connection(self):
        """
        Close HTTPS connection
        """
        try:
            self.log_debug("Closing HTTPS connection")
            
            # Stop HTTP service
            response, success = self._send_command_interrupt("AT+QHTTPSTOP", wait_time=5000)
            if not success:
                self.log_info("Failed to stop HTTP service cleanly")
            

            self.log_debug("HTTPS connection closed")
            
        except Exception as e:
            self.log_error(f"Error occured while closing HTTPS connection: {e}")

    def list_files(self):
        """
        List all files in EG91 FS with improved error handling.

        :return list: string list of all file names
        """
        try:
            self.log_debug("Listing all files in UFS:")
            response, success = self._send_command_interrupt('AT+QFLST="UFS:*"')
            
            if not success:
                self.log_error("Failed to list files")
                return None
                
            files = []
            if "+QFLST" in response:
                lines = response.split("\n")
                for line in lines:
                    if "+QFLST" in line:
                        self.log_debug(line)
                        # Extract filename from the response
                        # Format: +QFLST: "UFS:filename",size
                        try:
                            filename_part = line.split('"')[1].replace("UFS:", "")
                            files.append(filename_part)
                        except (IndexError, ValueError):
                            self.log_info(f"Could not parse file line: {line}")
                return files
            else:
                self.log_info("No files found in UFS")
                return []
                
        except Exception as e:
            self.log_error(f"Error listing files: {e}")
            return None

    def read_file(self, filename: str) -> str:
        """
        Read file in EG91 FS with improved error handling.

        :param str filename: file name without "UFS:" prefix
        :return str: file content as a string
        """
        try:
            self.log_debug(f"Reading content of file {filename}")
            command = f'AT+QFOPEN="UFS:{filename}",0'
            response, success = self._send_command_interrupt(command)

            if not success or "+QFOPEN:" not in response:
                self.log_error(f"Error opening file {filename}: {response}")
                return None

            # Extract file handle
            try:
                file_handle = response.split(":")[1].strip().split()[0]
                self.log_debug(f"File handle: {file_handle}")
            except (IndexError, ValueError):
                self.log_error(f"Could not extract file handle from: {response}")
                return None

            # Read file content
            read_command = f'AT+QFREAD={file_handle},1024'
            response, success = self._send_command_interrupt(read_command)

            content = None
            if success and "+QFREAD" in response:
                # Extract content from response
                lines = response.split("\n")
                content_lines = []
                content_started = False
                
                for line in lines:
                    if "+QFREAD:" in line:
                        content_started = True
                        continue
                    elif content_started and line.strip() != "OK":
                        content_lines.append(line)
                        
                content = "\n".join(content_lines).strip()
                self.log_debug(f"Content of file {filename}:\n{content}")
            else:
                self.log_error(f"Error reading file {filename}: {response}")

            # Close file
            close_command = f'AT+QFCLOSE={file_handle}'
            close_response, close_success = self._send_command_interrupt(close_command)
            if close_success:
                self.log_debug(f"File {filename} closed.")
            else:
                self.log_info(f"Failed to close file {filename}: {close_response}")

            return content

        except Exception as e:
            self.log_error(f"Error reading file {filename}: {e}")
            return None

    def delete_file(self, filename: str) -> bool:
        """
        Delete file from EG91 FS with improved error handling.

        :param str filename: file name without "UFS:" prefix
        :return bool: True if the file has been successfully deleted, otherwise False
        """
        try:
            self.log_debug(f"Deleting {filename}")
            command = f'AT+QFDEL="UFS:{filename}"'
            response, success = self._send_command_interrupt(command)

            if success:
                self.log_debug(f"File {filename} successfully deleted.")
                return True
            else:
                self.log_error(f"Error deleting {filename}: {response}")
                return False

        except Exception as e:
            self.log_error(f"Error deleting file {filename}: {e}")
            return False

    def delete_all_files(self):
        """
        Delete all files from EG91 FS with improved error handling.

        :return bool: True if all files have been successfully deleted, otherwise False
        """
        try:
            self.log_info("Deleting all files from UFS")
            response, success = self._send_command_interrupt('AT+QFLST="UFS:*"')
            
            if not success:
                self.log_error("Error listing files for deletion")
                return False

            success_count = 0
            total_files = 0

            if "+QFLST" in response:
                lines = response.split("\n")
                for line in lines:
                    if "+QFLST" in line:
                        try:
                            # Extract filename: +QFLST: "UFS:filename",size
                            filename = line.split('"')[1].replace("UFS:", "")
                            total_files += 1
                            if self.delete_file(filename):
                                success_count += 1
                        except (IndexError, ValueError):
                            self.log_info(f"Could not parse file line for deletion: {line}")

                if total_files == 0:
                    self.log_info("No files found to delete")
                    return True
                elif success_count == total_files:
                    self.log_info(f"Successfully deleted all {total_files} files")
                    return True
                else:
                    self.log_error(f"Only deleted {success_count} out of {total_files} files")
                    return False
            else:
                self.log_info("No files found in UFS")
                return True

        except Exception as e:
            self.log_error(f"Error deleting all files: {e}")
            return False
    
    def log_info(self, message: str):
        print(f"[EG91] {message}")
    
    def log_error(self, message: str):
        RED     = "\033[31m"
        RESET   = "\033[0m"
        print(f"{RED}[EG91 ERROR] {message}{RESET}")
    
    def log_debug(self, message: str):
        YELLOW     = "\033[33m"
        RESET   = "\033[0m"
        print(f"{YELLOW}[EG91 DEBUG] {message}{RESET}")

    def get_signal_quality(self):
        """
        Check signal quality (RSSI and BER)
        :return tuple | None: (rssi, rsrp, rsrq, sinr) or None if error occurs
        """
        try:
            response, success = self._send_command_interrupt('AT+QENG="servingcell"')
            if not success:
                self.log_error("Failed to get signal quality")
                return None

            if "+QENG:" in response:
                line = response.splitlines()[1]
                #self.log_debug(f"QENG response line: {line}")
                parts = line.split(":")[1].strip().split(",")
                #self.log_debug(f"QENG response parts: {parts}")
                if parts[2]=='"LTE"':
                    rsrp = int(parts[-5])
                    rsrq = int(parts[-4])
                    rssi = int(parts[-3])
                    sinr = int(parts[-2])
                    self.log_info(f"Signal Quality - RSSI: {rssi}, RSRP: {rsrp}, RSRQ: {rsrq}, SINR: {sinr}")
                    return (rssi, rsrp, rsrq, sinr)
                else:
                    self.log_error("This module is not in LTE mode")
                    return None
            else:
                self.log_error("Unexpected response format for QENG")
                return None

        except Exception as e:
            self.log_error(f"Error checking signal quality: {e}")
            return None
