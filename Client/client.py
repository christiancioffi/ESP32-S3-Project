from machine import Pin
from machine import Pin, I2S, SDCard, idle, UART
import network
import urequests as requests
import ntptime, time
import math
import struct
import sys
from eg91_sender_v3 import Eg91SenderV3 
import ujson
import os
import esp

ENDPOINT="https://webhook.site/a8897191-bff0-4267-88eb-803b739cf5d9/audio"
NODEID=str(0)  #ID del nodo
LTE_RESET_PIN = 3
LTE_POWER_PIN = 4
UART_TX_PIN = 41
UART_RX_PIN = 42
SCK_PIN = 46
WS_PIN = 47
SD_PIN = 48

class Metadata:
    def __init__(self, tmst: int, noId: str, blvl: float, rmsv: float):
        self.tmst = tmst
        self.noId = noId
        self.blvl = blvl
        self.rmsv = rmsv

    def to_dict(self) -> dict:
        return {
            "tmst": self.tmst,
            "noId": self.noId,
            "blvl": self.blvl,
            "rmsv": self.rmsv
        }


def getCurrentDate():
    timestamp=time.localtime(time.time()+3600)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(timestamp[0], timestamp[1], timestamp[2], timestamp[3], timestamp[4], timestamp[5])

def setupWiFiConnection():
    SSID='SSID'
    KEY='KEY'
    try:
        wlan = network.WLAN(network.WLAN.IF_STA)
        wlan.active(True)
        if not wlan.isconnected():
            print('Connecting to network...')
            wlan.connect(SSID, KEY)
            while not wlan.isconnected():
                idle()
        print('Network config:', wlan.ipconfig('addr4'))
        #wlan.disconnect()
        ntptime.settime()
        print("["+getCurrentDate()+"] "+"Wi-Fi configuration completed")
        return True
    except Exception as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        print("Wi-Fi configuration not completed")
        return False

def setupLTEConnection():
    try:
        config={}
        with open("config.json") as f:
            config = ujson.loads(f.read())
        
        uart = UART(1, baudrate=115200, tx=Pin(UART_TX_PIN), rx=Pin(UART_RX_PIN), timeout=3000)

        eg91 = Eg91SenderV3(uart, config)

        # Power cycle
        Pin(LTE_POWER_PIN, Pin.OUT).on()
        time.sleep(1)
        Pin(LTE_POWER_PIN, Pin.OUT).off()
        time.sleep(15)

        if eg91.enable():
            print(f"Connection status: {eg91.get_mqtt_connection_status()}")
            print(f"Reading data from topic: {eg91.get_mqtt_data()}")
            print(f"Current time: {eg91.get_time()}")
            return eg91
        else:
            print("Failed to enable EG91 sender\n")
        
        return None
    except OSError:
        print("Configuration file not found")
        config = {}
        return None
    except Exception as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        print("Failed to setup LTE connection")
        return None
    

def closeLTEConnection(eg91):
    try:
        eg91.disable()
        Pin(LTE_POWER_PIN, Pin.OUT).on()
        time.sleep(1)
        Pin(LTE_POWER_PIN, Pin.OUT).off()
        time.sleep(15)
        print("LTE connection closed successfully")
    except Exception as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        print("Failed to close LTE connection")

def isNetworkQualityGood(eg91):
    try:
        signal_strength_values = eg91.get_signal_quality()
        if signal_strength_values is None:
            return False
        else:
            rssi, rsrp, rsrq, sinr = signal_strength_values
            if rsrp >= -95 and sinr >= 10 and rsrq >= -15:
                print("Network quality is good")
                return True
            else:
                print("Network quality is poor")
                return False
    except Exception as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        print("Failed to assess network quality")
        return False

def calculateRMS(audio_bytes, bits_per_sample, num_channels):
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
    print("RMS value calculated:", rmsv)
    return rmsv

def getMetadata(chunk, bits_per_sample, num_channels):
    timestamp=str(time.time()+3600)
    nodeID=NODEID
    batteryLevel="100%" #TO-DO
    rmsv=calculateRMS(chunk, bits_per_sample, num_channels)
    return Metadata(
         tmst=timestamp,
         noId=nodeID,
         blvl=batteryLevel,
         rmsv=rmsv).to_dict()

def create_wav_header(sampleRate, bitsPerSample, num_channels, num_samples, metadata=None):
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
            print("Adding metadata key:", key, "value:", value)
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

def getSingleAudioChunk():
    #sd = SDCard(slot=2)  # sck=18, mosi=23, miso=19, cs=5
    #os.mount(sd, "/sd")

    # MICROPHONE = Adafruit I2S MEMS Microphone Breakout - SPH0645LM4H

    # ======= I2S CONFIGURATION =======
    I2S_ID = 0
    BUFFER_LENGTH_IN_BYTES = 40000

    # ======= AUDIO CONFIGURATION =======
    #WAV_FILE = "mic.wav"
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

    wav_data=bytes()

    #wav = open("/sd/{}".format(WAV_FILE), "wb")

    audio_in = I2S(
        I2S_ID,
        sck=Pin(SCK_PIN),
        ws=Pin(WS_PIN),
        sd=Pin(SD_PIN),
        mode=I2S.RX,
        bits=WAV_SAMPLE_SIZE_IN_BITS,
        format=FORMAT,
        rate=SAMPLE_RATE_IN_HZ,
        ibuf=BUFFER_LENGTH_IN_BYTES,
    )

    # allocate sample arrays
    # memoryview used to reduce heap allocation in while loop
    mic_samples = bytearray(10000)
    mic_samples_mv = memoryview(mic_samples)

    num_sample_bytes_written_to_wav = 0

    print("Recording size: {} bytes".format(RECORDING_SIZE_IN_BYTES))
    print("==========  START RECORDING ==========")
    try:
        while num_sample_bytes_written_to_wav < RECORDING_SIZE_IN_BYTES:
            # read a block of samples from the I2S microphone
            num_bytes_read_from_mic = audio_in.readinto(mic_samples_mv)
            '''
            for i in range(0, len(mic_samples_mv), 4):
                b0 = mic_samples_mv[i]
                b1 = mic_samples_mv[i+1]
                b2 = mic_samples_mv[i+2]
                b3 = mic_samples_mv[i+3]
                print(i, ":", hex(b0), hex(b1), hex(b2), hex(b3))
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

        print("==========  DONE RECORDING ==========")
    except (KeyboardInterrupt, Exception) as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        wav_data=bytes()
    finally:
        audio_in.deinit()
    # cleanup
    #wav.close()
    #os.umount("/sd")
    #sd.deinit()

    metadata=getMetadata(wav_data, WAV_SAMPLE_SIZE_IN_BITS, NUM_CHANNELS)

    # create header for WAV file and write to SD card
    wav_header = create_wav_header(
        SAMPLE_RATE_IN_HZ,
        WAV_SAMPLE_SIZE_IN_BITS,
        NUM_CHANNELS,
        SAMPLE_RATE_IN_HZ * RECORD_TIME_IN_SECONDS,
        metadata
    )
    
    wav_chunk=wav_header+wav_data

    return wav_chunk

def sendChunkToServer(sender, chunk):
    print("["+getCurrentDate()+"] "+"Sending chunk to the server")
    try:
        response = sender.https_post_request(url=ENDPOINT,body=chunk,content_type="application/octet-stream")
        #response = sender.https_get_request(url=ENDPOINT)
        print("[APP] Response:", response)
    except Exception as e:
        print("Caught exception {} {}".format(type(e).__name__, e))
        print("["+getCurrentDate()+"] "+"Chunk not sent")


LTESender=setupLTEConnection()
if LTESender:
    chunk=getSingleAudioChunk()
    if len(chunk)>0 and isNetworkQualityGood(LTESender):
        sendChunkToServer(LTESender, chunk)
        
    closeLTEConnection(LTESender)


