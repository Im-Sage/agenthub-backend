import math


class TokenEstimator:
    def estimate(self, text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens < 1:
            return ""
        if self.estimate(text) <= max_tokens:
            return text
        return text[: max_tokens * 4]
