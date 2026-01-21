from machine import Pin, I2S, idle, UART, SoftI2C
import network
import urequests as requests
import time
import math
import struct
import sys
from eg91_sender_v3 import Eg91SenderV3 
import ujson
from SDBuffer import SDBuffer
from soc_driver import SocDriver
from bus_service import I2cAdapter



class Metadata:
    def __init__(self, noId: str, blvl: float, rmsv: float):
        self.noId = noId
        self.blvl = blvl
        self.rmsv = rmsv

    def to_dict(self) -> dict:
        return {
            "noId": self.noId,
            "blvl": self.blvl,
            "rmsv": self.rmsv
        }


class AudioAliNode:

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
    IDLE_TIME = 10  # seconds

    def __init__(self):

        self.sd_buffer = SDBuffer(
            spi_id=1,
            sck_pin=self.SPI_SCK_PIN,
            mosi_pin=self.SPI_MOSI_PIN,
            miso_pin=self.SPI_MISO_PIN,
            cs_pin=self.SPI_CS_PIN,
            files_threshold=1
        )   #15 GB buffer size

        self.LTESender = None
        self.i2s_audio_in = None

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

    def setup_LTE_Connection(self):
        try:
            config={}
            try:
                with open("config.json") as f:
                    config = ujson.loads(f.read())
            except Exception as e:
                raise OSError("Failed to read configuration file: {}".format(e))
            
            uart = UART(1, baudrate=115200, tx=Pin(self.UART_TX_PIN), rx=Pin(self.UART_RX_PIN), timeout=3000)

            eg91 = Eg91SenderV3(uart, config)

            # Power cycle
            Pin(self.LTE_POWER_PIN, Pin.OUT).on()
            time.sleep(1)
            Pin(self.LTE_POWER_PIN, Pin.OUT).off()
            time.sleep(15)

            if eg91.enable():
                self.log_info(f"Current time: {eg91.get_time()}")
                self.LTESender = eg91
            else:
                self.log_info("Failed to enable EG91 sender\n")
        except OSError as e:
            self.log_error(e)
            config = {}
        except Exception as e:
            self.log_error("Caught exception {} {}".format(type(e).__name__, e))
            self.log_error("Failed to setup LTE connection")

    def close_LTE_Connection(self):
        try:
            eg91=self.LTESender
            self.LTESender = None
            eg91.disable()
            Pin(self.LTE_POWER_PIN, Pin.OUT).on()
            time.sleep(1)
            Pin(self.LTE_POWER_PIN, Pin.OUT).off()
            time.sleep(15)
            self.log_info("LTE connection closed successfully")
        except Exception as e:
            self.log_error("Caught exception {} {}".format(type(e).__name__, e))
            self.log_error("Failed to close LTE connection")

    def is_network_quality_good(self):
        if self.LTESender:
            try:
                signal_strength_values = self.LTESender.get_signal_quality()
                if signal_strength_values is None:
                    return False
                else:
                    rssi, rsrp, rsrq, sinr = signal_strength_values
                    if rsrp >= -95 and sinr >= 10 and rsrq >= -15:
                        self.log_info("Network quality is good")
                        return True
                    else:
                        self.log_info("Network quality is poor")
                        return False
            except Exception as e:
                self.log_error("Caught exception {} {}".format(type(e).__name__, e))
                self.log_error("Failed to assess network quality")
                return False
        else:
            self.log_error("LTESender not initialized")
            return False

    def is_battery_sufficient(self):
        #data = self.soc_driver.get_data()
        #self.log_info("Battery data: {}%".format(data))
        return True

    def calculate_RMS(self, audio_bytes, bits_per_sample, num_channels):
        bytes_per_sample = bits_per_sample // 8
        frame_size = bytes_per_sample * num_channels
        num_frames = len(audio_bytes) // frame_size

        if num_frames == 0:
            return 0.0

        sum_squares = 0.0
        sample_count = 0

        for i in range(0, num_frames * frame_size, bytes_per_sample):
            sample_bytes = audio_bytes[i:i + bytes_per_sample]

            if bits_per_sample == 8:
                # 8-bit PCM è unsigned
                sample = sample_bytes[0] - 128  # trasforma in signed (-128..127)

            elif bits_per_sample == 16:
                # little-endian signed short
                sample = struct.unpack("<h", sample_bytes)[0]

            elif bits_per_sample == 24:
                # little-endian signed 24-bit
                # aggiungiamo un byte di estensione di segno manuale
                b = sample_bytes
                if b[2] & 0x80:
                    b += b'\xff'  # se il bit più alto è 1 -> negativo
                else:
                    b += b'\x00'  # altrimenti positivo
                sample = struct.unpack("<i", b)[0] >> 8

            elif bits_per_sample == 32:
                # little-endian signed int
                sample = struct.unpack("<i", sample_bytes)[0]

            else:
                raise ValueError("bits_per_sample non supportato")

            sum_squares += sample * sample
            sample_count += 1

        mean_square = sum_squares / sample_count
        rmsv=math.sqrt(mean_square)
        self.log_info(f"RMS value calculated: {rmsv}")
        return rmsv

    def get_metadata(self, chunk, bits_per_sample, num_channels):
        #timestamp=str(time.time()+3600)
        nodeID=self.NODEID
        batteryLevel="100%" #TODO
        rmsv=self.calculate_RMS(chunk, bits_per_sample, num_channels)
        return Metadata(
            #tmst=timestamp,
            noId=nodeID,
            blvl=batteryLevel,
            rmsv=rmsv).to_dict()

    def create_wav_header(self, sampleRate, bitsPerSample, num_channels, num_samples, metadata=None):
        datasize = num_samples * num_channels * bitsPerSample // 8

        o = b"RIFF"
        filesize_pos = len(o)
        o += (0).to_bytes(4, "little")  # placeholder dimensione RIFF
        o += b"WAVE"

        # fmt chunk (PCM)
        o += b"fmt "
        o += (16).to_bytes(4, "little")
        o += (1).to_bytes(2, "little")  # AudioFormat = PCM
        o += num_channels.to_bytes(2, "little")
        o += sampleRate.to_bytes(4, "little")
        o += (sampleRate * num_channels * bitsPerSample // 8).to_bytes(4, "little")
        o += (num_channels * bitsPerSample // 8).to_bytes(2, "little")
        o += bitsPerSample.to_bytes(2, "little")

        # LIST / INFO chunk (metadati)
        if metadata:
            info_data = b"INFO"

            for key, value in metadata.items():
                self.log_info(f"Adding metadata key: {key} value: {value}")
                if len(key) != 4:
                    raise ValueError("Le chiavi INFO devono essere di 4 caratteri")

                data = str(value).encode("ascii") + b"\x00"

                if len(data) % 2 == 1:
                    data += b"\x00"  # padding a 2 byte

                info_data += key.encode("ascii")
                info_data += len(data).to_bytes(4, "little")
                info_data += data

            o += b"LIST"
            o += len(info_data).to_bytes(4, "little")
            o += info_data

        # data chunk
        o += b"data"
        o += datasize.to_bytes(4, "little")

        # aggiorna dimensione RIFF (file_size - 8)
        filesize = len(o) - 8 + datasize
        o = o[:filesize_pos] + filesize.to_bytes(4, "little") + o[filesize_pos + 4:]

        return o

    def get_single_audio_chunk(self):

                # MICROPHONE = Adafruit I2S MEMS Microphone Breakout - SPH0645LM4H

        # ======= I2S CONFIGURATION =======
        I2S_ID = 0
        BUFFER_LENGTH_IN_BYTES = 40000

        # ======= AUDIO CONFIGURATION =======
        RECORD_TIME_IN_SECONDS = 5  #10
        WAV_SAMPLE_SIZE_IN_BITS = 32        #Slot bit width
        FORMAT = I2S.MONO
        SAMPLE_RATE_IN_HZ = 16_000  #Sampling rate = 16kHz


        format_to_channels = {I2S.MONO: 1, I2S.STEREO: 2}
        NUM_CHANNELS = format_to_channels[FORMAT]
        WAV_SAMPLE_SIZE_IN_BYTES = WAV_SAMPLE_SIZE_IN_BITS // 8
        RECORDING_SIZE_IN_BYTES = (
            RECORD_TIME_IN_SECONDS * SAMPLE_RATE_IN_HZ * WAV_SAMPLE_SIZE_IN_BYTES * NUM_CHANNELS
        )

        # ======= I2S INITIALIZATION =======
        self.i2s_audio_in = I2S(
            I2S_ID,
            sck=Pin(self.I2S_SCK_PIN),
            ws=Pin(self.I2S_WS_PIN),
            sd=Pin(self.I2S_SD_PIN),
            mode=I2S.RX,
            bits=WAV_SAMPLE_SIZE_IN_BITS,
            format=FORMAT,
            rate=SAMPLE_RATE_IN_HZ,
            ibuf=BUFFER_LENGTH_IN_BYTES,
        )

        wav_data=bytes()

        # allocate sample arrays
        # memoryview used to reduce heap allocation in while loop
        mic_samples = bytearray(10000)
        mic_samples_mv = memoryview(mic_samples)

        num_sample_bytes_written_to_wav = 0

        self.log_info("Recording size: {} bytes".format(RECORDING_SIZE_IN_BYTES))
        self.log_info("==========  START RECORDING ==========")
        try:
            while num_sample_bytes_written_to_wav < RECORDING_SIZE_IN_BYTES:
                # read a block of samples from the I2S microphone
                num_bytes_read_from_mic = self.i2s_audio_in.readinto(mic_samples_mv)
                '''
                for i in range(0, len(mic_samples_mv), 4):
                    b0 = mic_samples_mv[i]
                    b1 = mic_samples_mv[i+1]
                    b2 = mic_samples_mv[i+2]
                    b3 = mic_samples_mv[i+3]
                    self.log_info(i, ":", hex(b0), hex(b1), hex(b2), hex(b3))
                '''
                if num_bytes_read_from_mic > 0:
                    num_bytes_to_write = min(
                        num_bytes_read_from_mic, RECORDING_SIZE_IN_BYTES - num_sample_bytes_written_to_wav
                    )
                    # write samples to WAV file
                    #num_bytes_written = wav.write(mic_samples_mv[:num_bytes_to_write])
                    wav_data+=mic_samples_mv[:num_bytes_to_write]
                    num_bytes_written = num_bytes_to_write
                    num_sample_bytes_written_to_wav += num_bytes_written

            self.log_info("==========  DONE RECORDING ==========")
        except (KeyboardInterrupt, Exception) as e:
            self.log_error("Caught exception {} {}".format(type(e).__name__, e))
            wav_data=bytes()
        finally:
            self.i2s_audio_in.deinit()
        # cleanup
        #wav.close()
        #os.umount("/sd")
        #sd.deinit()

        metadata=self.get_metadata(wav_data, WAV_SAMPLE_SIZE_IN_BITS, NUM_CHANNELS)

        # create header for WAV file and write to SD card
        wav_header = self.create_wav_header(
            SAMPLE_RATE_IN_HZ,
            WAV_SAMPLE_SIZE_IN_BITS,
            NUM_CHANNELS,
            SAMPLE_RATE_IN_HZ * RECORD_TIME_IN_SECONDS,
            metadata
        )
        
        wav_chunk=wav_header+wav_data

        return wav_chunk

    def send_chunk_to_server(self, chunk):
        endpoint="http://ec2-18-197-151-12.eu-central-1.compute.amazonaws.com:8443/"
        #response = self.LTESender.https_post_request(url=endpoint,body=chunk,content_type="application/octet-stream")
        response = self.LTESender.https_get_request(url=endpoint)
        if response:
            self.log_info("Response: {}".format(response))
        else:
            raise Exception("An error occured while sending the chunk to the server")
    
    def log_info(self, message):
        GREEN     = "\033[32m"
        RESET   = "\033[0m"
        print(f"{GREEN}[AliNode] {message}{RESET}")

    def log_error(self, message):
        RED     = "\033[31m"
        RESET   = "\033[0m"
        print(f"{RED}[AliNode] {message}{RESET}")

    def start(self):
        while True:
            try:
                chunk=self.get_single_audio_chunk()
                self.sd_buffer.enqueue(chunk)
                is_buffer_full_enough=self.sd_buffer.is_buffer_full_enough()
                if is_buffer_full_enough and self.is_battery_sufficient():
                    self.log_info("Buffer full enough to send data (files: {})".format(self.sd_buffer.get_number_of_files()))
                    buffer_len=self.sd_buffer.get_number_of_files()
                    for _ in range(buffer_len):
                        try:
                            chunk=self.sd_buffer.dequeue()
                            if chunk:
                                self.log_info("Chunk sent to the server...")
                        except Exception as e:
                            break
                    '''
                    self.setup_LTE_Connection()
                    if self.LTESender and self.is_battery_sufficient():
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
                        self.close_LTE_Connection()
                    '''
                else:
                    self.log_info("Buffer NOT full enough to send data (files: {})".format(self.sd_buffer.get_number_of_files()))
                    
            except Exception as e:
                self.log_error("A problem during this iteration: {}".format(e))
            
            self.log_info("Sleeping...")
            time.sleep(self.IDLE_TIME)
            self.log_info("Awake!")


alinode=AudioAliNode()
alinode.start()


