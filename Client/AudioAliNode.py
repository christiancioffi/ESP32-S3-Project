import sys
from Loggable import Loggable
from machine import Pin, I2S, idle, UART, SoftI2C, RTC
import time
import math
import struct
from eg91_sender_v5 import Eg91Sender 
import ujson
from sd_buffer import SDBuffer
from soc_driver import SocDriver
from bus_service import I2cAdapter
import ure
from wav_metadata import WAVMetadata
from i2s_driver import I2SDriver

class AudioAliNode(Loggable):

    NODEID="alinodev-1"
    LTE_RESET_PIN = 3
    LTE_POWER_PIN = 4
    UART_TX_PIN = 41
    UART_RX_PIN = 42
    I2S_SCK_PIN = 46
    I2S_WS_PIN = 47
    I2S_SD_PIN = 48
    SPI_SCK_PIN = 12
    SPI_MOSI_PIN = 11
    SPI_MISO_PIN = 13
    SPI_CS_PIN = 1
    IDLE_TIME = 20  # seconds
    FILES_THRESHOLD = 1

    def __init__(self):

        super().__init__(tag=AudioAliNode.__name__, info_color=Loggable.GREEN)

        self.sd_buffer = SDBuffer(
            spi_id=1,
            sck_pin=self.SPI_SCK_PIN,
            mosi_pin=self.SPI_MOSI_PIN,
            miso_pin=self.SPI_MISO_PIN,
            cs_pin=self.SPI_CS_PIN,
            files_threshold=self.FILES_THRESHOLD
        )   #15 GB buffer size

        self.lte_sender = None

        self.i2s_driver=I2SDriver(
            alinode=self,
            i2s_sck_pin=self.I2S_SCK_PIN,
            i2s_ws_pin=self.I2S_WS_PIN,
            i2s_sd_pin=self.I2S_SD_PIN
        )

        '''
        json_config="""{
            "adapter" : "i2c",
            "i2c_address": "0x55",
            "registry": {
                "v_raw": "106",
                "a_raw": "107",
                "ai": "108",
                "volt": "109",
                "soc": "110",
                "nom_capacity": "111",
                "full_available_capacity": "112",
                "remain_capacity": "114",
                "avg_power": "115",
                "soh": "118",
                "temperature": "119",
                "cycle_count": "120",
                "passed_charge": "121",
                "dod0": "122",
                "discharge_current": "123",
                "full_charge_capacity": "124"
            }
        }"""


        i2c = SoftI2C(scl=Pin(9), sda=Pin(8), freq=100000)
        adapter = I2cAdapter(i2c)
        config = ujson.loads(json_config)

        self.log_info(f"I2C scan: {i2c.scan()}")
        
        soc = SocDriver(adapter, config)
        #soc.data_memory_test()
        #soc.start()
        soc.write_qmax_cell_0()
        '''

        # For time synchronization at startup
        self.initialize_LTE_module()
        self.deinitialize_LTE_module()
            

    def deinit(self):
        self.sd_buffer.deinit()
        self.deinitialize_LTE_module()

    def get_node_id(self):
        return self.NODEID

    def initialize_LTE_module(self):
        try:

            eg91 = Eg91Sender(self.UART_TX_PIN, self.UART_RX_PIN, self.LTE_POWER_PIN, self.LTE_RESET_PIN)

            self.lte_sender = eg91

            current_time, _ = self.lte_sender.get_time()
            if current_time:
                try:
                    self.synchronize_time(current_time)
                    self.log_info(f"Local clock synchronized: {self.get_current_time()}")
                except Exception as e:
                    raise Exception("Failed to synchronize local clock: \"{}\"".format(e))
            else:
                raise Exception("Failed to get current time from network")
        except (KeyboardInterrupt, Exception) as e:
            self.log_error("Failed to initialize LTE module: \"{}\"".format(e))
            self.deinitialize_LTE_module()

    def deinitialize_LTE_module(self):
        if self.lte_sender:
            try:
                self.lte_sender.deinit()
                self.lte_sender = None
                self.log_info("LTE module deinitialized successfully")
            except (KeyboardInterrupt,Exception) as e:
                self.log_error("Failed to deinitialize LTE module: \"{}\"".format(e))

    def get_battery_level(self):
        battery_level = "100%"
        return battery_level

    def is_battery_sufficient(self):
        #data = self.soc_driver.get_data()
        #self.log_info("Battery data: {}%".format(data))
        return True

    def send_chunk_to_server(self, chunk):
        #endpoint="http://ec2-18-197-151-12.eu-central-1.compute.amazonaws.com:8443/audio"
        endpoint="https://tesi.aliagrid.com/audio"
        response = self.lte_sender.https_post_request(url=endpoint,body=chunk,content_type="application/octet-stream")
        #response = self.LTESender.https_get_request(url=endpoint)
        if response:
            self.log_info("Response: {}".format(response))
        else:
            raise Exception("An error occurred while sending the chunk to the server")
    
    def synchronize_time(self, current_time):
        try:
            regex_string=ure.compile(
                "(\d\d\d\d)/(\d\d)/(\d\d),"   # yyyy/MM/dd
                "(\d\d):(\d\d):(\d\d)"        # hh:mm:ss
                "([+-]\d+)"                   # ±zz
            )
            m = ure.match(regex_string,current_time)

            if not m:
                raise ValueError("Invalid time format")

            year   = int(m.group(1))
            month  = int(m.group(2))
            day    = int(m.group(3))
            hour   = int(m.group(4))
            minute = int(m.group(5))
            second = int(m.group(6))
            #zz     = int(m.group(7))

            rtc = RTC()
            rtc.datetime((
                year, month, day, 0,  # weekday = 0, lo puoi calcolare se vuoi
                hour, minute, second,
                0                     # subseconds
            ))

        except Exception as e:
            raise Exception("Error while synchronizing local clock: {}".format(e))
    
    def get_current_time(self):
        tm=time.localtime(time.time())
        current_time = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(tm[0], tm[1], tm[2], tm[3], tm[4], tm[5])
        return current_time
    
    def start(self):
        self.log_info(f"Starting main loop at time: {self.get_current_time()}")
        while True:
            try:
                chunk=self.i2s_driver.get_single_audio_chunk()
                self.sd_buffer.enqueue(chunk)
                is_buffer_full_enough=self.sd_buffer.is_buffer_full_enough()
                if is_buffer_full_enough and self.is_battery_sufficient():
                    self.log_info("Buffer full enough to send data (files: {})".format(self.sd_buffer.get_number_of_files()))
                    self.initialize_LTE_module()
                    if self.lte_sender:
                        self.log_info("Network connection established and battery sufficient, sending data...")
                        buffer_len=self.sd_buffer.get_number_of_files()
                        for _ in range(buffer_len):
                            self.log_info("Sending chunk {}/{}...".format(_+1, buffer_len))
                            try:
                                chunk=self.sd_buffer.dequeue()
                                if chunk:
                                    self.send_chunk_to_server(chunk)
                            except Exception as e:
                                break               # continue se invece vuoi continuare ad inviare il resto (o gestisci l'eccezione dentro send_chunk_to_server)
                        self.deinitialize_LTE_module()
                else:
                    self.log_info("Buffer NOT full enough to send data (files: {})".format(self.sd_buffer.get_number_of_files()))
            except Exception as e:
                self.log_error("A problem occurred during this iteration: \"{}\"".format(e))
            
            self.log_info("Sleeping...")
            time.sleep(self.IDLE_TIME)
            self.log_info("Awake!")


if __name__ == "__main__":
    alinode=None
    try:
        alinode=AudioAliNode()
        alinode.start()
        #alinode.sd_buffer.clear_buffer()
    except (KeyboardInterrupt, Exception) as e:
        sys.print_exception(e)
        pass
    finally:
        if alinode:
            alinode.log_info("Stopping AliNode...")
            alinode.deinit()
            alinode.log_info("AliNode stopped.")


