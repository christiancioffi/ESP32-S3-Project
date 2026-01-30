class Loggable():

    WHITE    = "\033[37m"
    RED     = "\033[31m"
    YELLOW     = "\033[33m"
    GREEN = "\033[32m"
    RESET   = "\033[0m"

    def __init__(self, tag: str, info_color: str = WHITE, error_color: str = RED, debug_color: str = YELLOW):
        self._tag = tag
        self._info_color = info_color
        self._error_color = error_color
        self._debug_color = debug_color

    def log_info(self, message: str):
        print(f"{self._info_color}[{self._tag} INFO] {message}{self.RESET}")
    
    def log_error(self, message: str):
        print(f"{self._error_color}[{self._tag} ERROR] {message}{self.RESET}")
    
    def log_debug(self, message: str):
        print(f"{self._debug_color}[{self._tag} DEBUG] {message}{self.RESET}")