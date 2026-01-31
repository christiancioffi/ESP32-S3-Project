class WAVMetadata:
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

