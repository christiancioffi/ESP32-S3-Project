#import os
from machine import Pin
from machine import Pin, I2S, SDCard, idle
import network
import urequests as requests
import json, binascii
import time

def configureWiFiConnection():
    SSID='SSID'     #TO-DO: leggere le credenziali da file locale
    KEY='KEY'
    wlan = network.WLAN(network.WLAN.IF_STA)
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect(SSID, KEY)
        while not wlan.isconnected():
            idle()
    print('network config:', wlan.ipconfig('addr4'))
    #wlan.disconnect()

def create_wav_header(sampleRate, bitsPerSample, num_channels, num_samples):
    datasize = num_samples * num_channels * bitsPerSample // 8
    o = bytes("RIFF", "ascii")  # (4byte) Marks file as RIFF
    o += (datasize + 36).to_bytes(
        4, "little"
    )  # (4byte) File size in bytes excluding this and RIFF marker
    o += bytes("WAVE", "ascii")  # (4byte) File type
    o += bytes("fmt ", "ascii")  # (4byte) Format Chunk Marker
    o += (16).to_bytes(4, "little")  # (4byte) Length of above format data
    o += (1).to_bytes(2, "little")  # (2byte) Format type (1 - PCM)
    o += (num_channels).to_bytes(2, "little")  # (2byte)
    o += (sampleRate).to_bytes(4, "little")  # (4byte)
    o += (sampleRate * num_channels * bitsPerSample // 8).to_bytes(4, "little")  # (4byte)
    o += (num_channels * bitsPerSample // 8).to_bytes(2, "little")  # (2byte)
    o += (bitsPerSample).to_bytes(2, "little")  # (2byte)
    o += bytes("data", "ascii")  # (4byte) Data Chunk Marker
    o += (datasize).to_bytes(4, "little")  # (4byte) Data size in bytes
    return o

def getSingleAudioChunk():
    #sd = SDCard(slot=2)  # sck=18, mosi=23, miso=19, cs=5
    #os.mount(sd, "/sd")

    # ======= I2S CONFIGURATION =======
    SCK_PIN = 4
    WS_PIN = 5
    SD_PIN = 18
    I2S_ID = 0
    BUFFER_LENGTH_IN_BYTES = 40000
    # ======= I2S CONFIGURATION =======

    # ======= AUDIO CONFIGURATION =======
    #WAV_FILE = "mic.wav"
    RECORD_TIME_IN_SECONDS = 5  #10
    WAV_SAMPLE_SIZE_IN_BITS = 32        #Slot bit width
    FORMAT = I2S.MONO
    SAMPLE_RATE_IN_HZ = 22_050
    # ======= AUDIO CONFIGURATION =======

    format_to_channels = {I2S.MONO: 1, I2S.STEREO: 2}
    NUM_CHANNELS = format_to_channels[FORMAT]
    WAV_SAMPLE_SIZE_IN_BYTES = WAV_SAMPLE_SIZE_IN_BITS // 8
    RECORDING_SIZE_IN_BYTES = (
        RECORD_TIME_IN_SECONDS * SAMPLE_RATE_IN_HZ * WAV_SAMPLE_SIZE_IN_BYTES * NUM_CHANNELS
    )



    #wav = open("/sd/{}".format(WAV_FILE), "wb")

    # create header for WAV file and write to SD card
    wav_header = create_wav_header(
        SAMPLE_RATE_IN_HZ,
        WAV_SAMPLE_SIZE_IN_BITS,
        NUM_CHANNELS,
        SAMPLE_RATE_IN_HZ * RECORD_TIME_IN_SECONDS,
    )
    #num_bytes_written = wav.write(wav_header)
    wav_data=wav_header

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
        print("caught exception {} {}".format(type(e).__name__, e))

    # cleanup
    #wav.close()
    #os.umount("/sd")
    #sd.deinit()
    audio_in.deinit()
    return wav_data

def getCompleteChunk(chunk):
    wav_data_b64=binascii.b2a_base64(chunk).decode()
    timestamp=str(time.time())  #TO-DO
    nodeID=str(0)   #TO-DO
    batteryLevel="100%" #TO-DO
    SNR=str(0)  #TO-DO
    RSM=str(0)  #TO-DO
    return {
        "data": wav_data_b64,
        "timestamp": timestamp,
        "nodeId": nodeID,
        "batteryLevel": batteryLevel,
        "snr": SNR,
        "rsm": RSM
    }

def sendChunkToServer(chunk):
    data=getCompleteChunk(chunk)
    response=requests.post(url="http://192.168.1.11/audio", json=json.dumps(data))
    print(response.text)

configureWiFiConnection()
chunk=getSingleAudioChunk()
sendChunkToServer(chunk)



