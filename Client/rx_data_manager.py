import _thread
import asyncio
from Loggable import Loggable
from at_commands_parser import ATCommandsParser
import time

class RXDataManager(Loggable):

    def __init__(self):
        super().__init__(RXDataManager.__name__)
        self._last_response=""
        self._response_ready = False
        self._command_pending = False
        self._is_response_erroneous = False
        self._current_command = None
        self._response_lock = _thread.allocate_lock()
        self._event_loop=asyncio.get_event_loop()
        self._reception_tsf=asyncio.ThreadSafeFlag()
        self._parser=ATCommandsParser()

    def _reset_status(self):
        self._last_response = ""
        self._response_ready = False
        self._command_pending = False
        self._current_command = None
        self._is_response_erroneous = False
    
    def clear_state(self, caller: str = ""):
        self.log_info(f"({caller}) Waiting for lock (clearing RX Manager State)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (clearing RX Manager State)")
            self._reset_status()
        self.log_info(f"({caller}) Lock released (clearing RX Manager State)")
    
    
    def set_pending_state(self, command: str, caller: str = ""):
        self.log_info(f"({caller}) Waiting for lock (Pending state initialization)")
        with self._response_lock:
            self.log_info(f"({caller}) Lock acquired (Pending state initialization)")
            self._last_response = ""
            self._response_ready = False
            self._command_pending = True
            self._current_command = command
            self._is_response_erroneous=False
        self.log_info(f"({caller}) Lock released (Pending state initialization)")
    
    def handle_received_data(self, raw_data: bytes, caller: str = ""):
        self.log_info(f"({caller}) Data received!")
        self.log_info(f"({caller}) {len(raw_data)} bytes read from UART")
        self.log_debug(f"({caller}) Raw data: {raw_data}")
        self.log_info(f"({caller}) Waiting for lock (handling received data)")
        self._response_lock.acquire()
        #------------Lock acquired------------
        self.log_info(f"({caller}) Lock acquired (handling received data)")

        try:
            data=raw_data.decode('utf-8')
        except Exception as e:
            self._response_lock.release()
            #------------Lock released------------
            self.log_info(f"({caller}) Lock released (handling received data)")
            raise Exception(f"Error decoding received data")

        if self._command_pending:
            try:
                response, error = self._parser.parse_response(self._current_command, data)
            except Exception as e:
                self._response_lock.release()
                #------------Lock released------------
                self.log_info(f"({caller}) Lock released (handling received data)")
                raise Exception(str(e))
            if response:
                self._last_response = response
                self._response_ready = True
                self._is_response_erroneous = False
                self.log_debug(f"({caller}) Pending command response detected")
                self._reception_tsf.set()
            if error:
                self._last_response = error
                self._response_ready = True
                self._is_response_erroneous = True
                self.log_debug(f"({caller}) Pending command error detected")
                self._reception_tsf.set()
        else:
            self.log_debug(f"({caller}) No command pending, data ignored")
        self._response_lock.release()
        #------------Lock released------------
        self.log_info(f"({caller}) Lock released (handling received data)")
    
    def read_received_data(self, wait_time, caller: str = ""):
        try:
            self._event_loop.run_until_complete(self._wait_response(wait_time))
        except asyncio.TimeoutError:
            pass
        
        self.log_info(f"({caller}) Waiting for lock (response reading)")
        self._response_lock.acquire()
        #------------Lock acquired------------
        self.log_info(f"({caller}) Lock acquired (response reading)")
        self._reception_tsf.clear()
        if self._response_ready:
            response = self._last_response
            success = not self._is_response_erroneous
            self._reset_status()
        else:                       #Timeout occurred
            self._reset_status()
            self._response_lock.release()
            #------------Lock released------------
            raise Exception(f"Timeout occurred while waiting for a response")
        self._response_lock.release()
        #------------Lock released------------
        self.log_info(f"({caller}) Lock released (response reading)")

        return response, success
    
    async def _wait_response(self, timeout):
        self.log_debug(f"Waiting for response for {timeout} ms...")
        await asyncio.wait_for_ms(self._reception_tsf.wait(), timeout)
        time.sleep(0)                           #Per permettere, eventualmente, al callback handler di essere eseguito
        self.log_debug("Waiting terminated")

