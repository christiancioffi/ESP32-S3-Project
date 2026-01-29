import re


class ATCommandsParser():

    _ip_pattern=f"(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d){"(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d))"*3}"
    _simple_ip_pattern=f"\d(\d)?(\d)?{"\.\d(\d)?(\d)?"*3}"
    _simplest_ip_pattern=f"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"
    _time_pattern="\d\d\d\d/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01]),([01]\d|2[0-3]):([0-5]\d):([0-5]\d)[+-]\d\d"

    COMMAND_PATTERNS=[  # command : command pattern
        ("AT","AT"),
        ("ATE0","ATE0"),
        ("AT+CREG?","AT\+CREG\?"),
        ("AT+CPIN?","AT\+CPIN\?"),
        ("AT+QICSGP=<contextID>[,<context_type>,<APN>[,<username>,<password>[,<authentication>]]]","AT\+QICSGP=\d(\d)?(,[1-3],\".*\"(,\".*\",\".*\"(,[0-3])?)?)?"),
        ("AT+QIACT?","AT\+QIACT\?"),
        ("AT+QIACT=<contextID>","AT\+QIACT=\d(\d)?"),
        ("AT+QIDEACT=<contextID>","AT\+QIDEACT=\d(\d)?"),
        ("AT+QLTS=<mode>","AT\+QLTS=[0-2]"),
        ("AT+QHTTPCFG","AT\+QHTTPCFG=\".+\"(,\d(\d)?)"),
        ("AT+QSSLCFG=\"sni\",<sslctxID>[,<sni>]","AT\+QSSLCFG=\"sni\",\d(\d)?(,[0-1])?"),
        ("AT+QHTTPURL=<URL_length>[,<timeout>]","AT\+QHTTPURL=\d+(,\d+)?"),
        ("AT+QHTTPGET[=<rsptime>]","AT\+QHTTPGET(=\d+)?"),
        ("AT+QHTTPREAD[=<wait_time>]","AT\+QHTTPREAD(=\d+)?"),
        ("AT+QHTTPPOST=<data_length>[,<input_time>,<rsptime>]","AT\+QHTTPPOST=\d+(,\d+,\d+)?"),
        ("AT+QHTTPSTOP","AT\+QHTTPSTOP"),
        ("URL","http(s)?://.+"),
        ("BODY_POST",".+")
    ]

    RESPONSE_PATTERNS={ # command : response pattern
        'AT' : "AT\r\r\nOK\r\n",
        'ATE0': "ATE0\r\r\nOK\r\n",
        'AT+CREG?': f"\r\n\+CREG: \d+,\d+(,\"{"[0-9a-f]"*4}\",\"{"[0-9a-f]"*4}{"[0-9a-f]?"*3}\"(,\d+)?)?\r\n\r\nOK\r\n",
        'AT+CPIN?': "\r\n\+CPIN: (.+)\r\n\r\nOK\r\n",
        'AT+QICSGP=<contextID>[,<context_type>,<APN>[,<username>,<password>[,<authentication>]]]': "\r\nOK\r\n",
        'AT+QIACT?': f"(\r\n\+QIACT: \d(\d)?,\d,\d(,\"{_simplest_ip_pattern}\")?\r\n)?\r\nOK\r\n",
        'AT+QIACT=<contextID>': "\r\nOK\r\n",
        'AT+QIDEACT=<contextID>': "\r\nOK\r\n",
        'AT+QLTS=<mode>': f"\r\n\+QLTS: \"{_time_pattern},\d\"\r\n\r\nOK\r\n",
        'AT+QHTTPCFG': "\r\nOK\r\n",
        'AT+QSSLCFG="sni",<sslctxID>[,<sni>]': "\r\nOK\r\n",
        'AT+QHTTPURL=<URL_length>[,<timeout>]': "\r\nCONNECT\r\n",
        'AT+QHTTPGET[=<rsptime>]': "\r\nOK\r\n\r\n\+QHTTPGET: \d+(,\d+(,\d+)?)?\r\n",
        'AT+QHTTPREAD[=<wait_time>]': "\r\nCONNECT\r\n.*\r\nOK\r\n\r\n\+QHTTPREAD: \d+\r\n",
        'AT+QHTTPPOST=<data_length>[,<input_time>,<rsptime>]': "\r\nCONNECT\r\n",
        'AT+QHTTPSTOP': "\r\nOK\r\n",
        'URL': "\r\nOK\r\n",
        'BODY_POST': "\r\nOK\r\n\r\n\+QHTTPPOST: \d+(,\d+(,\d+)?)?\r\n",
        "ERROR": "\r\n(\+CME ERROR: \d+)|(ERROR)\r\n"
    }

    def __init__(self):
        self.COMMAND_PATTERNS = [
            (cmd, re.compile("^" + pattern + "$"))
            for cmd, pattern in self.COMMAND_PATTERNS
        ]

    def parse_response(self, command: str, data: str):

        recognized_command=self._get_recognized_command(command.strip())
        self.log_debug(f"Command pattern identified: {recognized_command}")
        response_pattern=self.RESPONSE_PATTERNS.get(recognized_command, None)
        response=None
        error=None

        if response_pattern is None:
            self.log_error(f"Unknown command: {command}")
            return None, None
        
        response_regex=re.compile(response_pattern) #TODO: controllare che funzioni
        matched_response=response_regex.search(data)

        if matched_response:
            self.log_debug(f"Matched response: {matched_response.group(0)}")
            response = matched_response.group(0)
        else:
            error_regex=re.compile(self.RESPONSE_PATTERNS["ERROR"])
            matched_error=error_regex.search(data)
            if matched_error:
                self.log_debug(f"Matched error response: {matched_error.group(0)}")
                error= matched_error.group(0)
            else:
                self.log_debug("No matching response or error found")
        
        return response, error
    

    def log_info(self, message: str):
        print(f"[ATParser] {message}")
    
    def log_error(self, message: str):
        RED     = "\033[31m"
        RESET   = "\033[0m"
        print(f"{RED}[ATParser ERROR] {message}{RESET}")
    
    def log_debug(self, message: str):
        YELLOW     = "\033[33m"
        RESET   = "\033[0m"
        print(f"{YELLOW}[ATParser DEBUG] {message}{RESET}")
    
    def _get_recognized_command(self, command: str) -> str:
        for cmd, pattern in self.COMMAND_PATTERNS:
            #pattern_regex=re.compile(f"^{pattern}$") #pattern
            if pattern.match(command):
                return cmd
        return None