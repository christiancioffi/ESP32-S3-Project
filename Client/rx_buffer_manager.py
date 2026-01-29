import _thread
import asyncio
from at_commands_parser import ATCommandsParser
import time

class RXBufferManager():

    def __init__(self, buffer_size=2048):
        self._rx_buffer_size = buffer_size
        self._rx_buffer=""
        self._response_ready = False
        self._command_pending = False
        self._is_response_erroneous = False
        self._current_command = None
        self._response_lock = _thread.allocate_lock()
        self._event_loop=asyncio.get_event_loop()
        self._reception_tsf=asyncio.ThreadSafeFlag()
        self._parser=ATCommandsParser()

    def set_pending_status(self, command: str, caller: str = ""):
        self.log_info(f"({caller}) Waiting for lock (buffer initialization)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (buffer initialization)")
            self._rx_buffer = ""
            self._response_ready = False
            self._command_pending = True
            self._current_command = command
            self._is_response_erroneous=False
        self.log_info(f"({caller}) Lock released (buffer initialization)")
    
    def insert_into_buffer(self, raw_data: bytes, caller: str = ""):
        self.log_info(f"({caller}) Data received!")
        self.log_info(f"({caller}) {len(raw_data)} bytes read from UART")
        self.log_debug(f"({caller}) Raw data: {raw_data}")
        self.log_info(f"({caller}) Waiting for lock (filling the buffer)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (filling the buffer)")

            try:
                data=raw_data.decode('utf-8')
            except Exception as e:
                raise Exception(f"Error decoding received data")

            if self._command_pending:
                try:
                    response, error = self._parser.parse_response(self._current_command, data)
                except Exception as e:
                    raise Exception(f"Error parsing response")
                if response:
                    self._rx_buffer += response
                    self._response_ready = True
                    self._is_response_erroneous = False
                    self.log_debug(f"({caller}) Pending command response detected")
                    self._reception_tsf.set()
                if error:
                    self._rx_buffer += error
                    self._response_ready = True
                    self._is_response_erroneous = True
                    self.log_debug(f"({caller}) Pending command error detected")
                    self._reception_tsf.set()
            else:
                self.log_debug(f"({caller}) No command pending, data ignored")
        self.log_info(f"({caller}) Lock released (filling the buffer)")
    
    def read_from_buffer(self, wait_time, caller: str = ""):
        self._event_loop.run_until_complete(self._wait_response(wait_time))
        self.log_info(f"({caller}) Waiting for lock (response reading)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (response reading)")
            self._reception_tsf.clear()
            if self._response_ready:
                response = self._rx_buffer
                success = not self._is_response_erroneous
                self._reset_status()
            else:
                current_command=self._current_command
                self._reset_status()
                raise Exception(f"Timeout waiting for response to command: {current_command}")
        self.log_info(f"({caller}) Lock released (response reading)")

        return response, success

    def _reset_status(self):
        self._rx_buffer = ""
        self._response_ready = False
        self._command_pending = False
        self._current_command = None
        self._is_response_erroneous = False
    
    def clear_buffer(self, caller: str = ""):
        self.log_info(f"({caller}) Waiting for lock (clearing buffer)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (clearing buffer)")
            self._rx_buffer = ""
        self.log_info(f"({caller}) Lock released (clearing buffer)")
    
    async def _wait_response(self, timeout):
        try:
            self.log_debug(f"Waiting for response for {timeout} ms...")
            await asyncio.wait_for_ms(self._reception_tsf.wait(), timeout)
            time.sleep(0)               #Per permettere, eventualmente, al callback handler di essere eseguito
            self.log_debug("Waiting terminated")
        except asyncio.TimeoutError:
            self.log_error("Timeout occurred")

    def log_info(self, message: str):
        print(f"[RXBufferManager] {message}")
    
    def log_error(self, message: str):
        RED     = "\033[31m"
        RESET   = "\033[0m"
        print(f"{RED}[RXBufferManager ERROR] {message}{RESET}")
    
    def log_debug(self, message: str):
        YELLOW     = "\033[33m"
        RESET   = "\033[0m"
        print(f"{YELLOW}[RXBufferManager DEBUG] {message}{RESET}")
