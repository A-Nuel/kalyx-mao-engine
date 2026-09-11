from pydantic import BaseModel, Field, field_validator

class Credits(BaseModel):
    amount: int = Field(ge=0, description="Non-negative credit amount")

    def __add__(self, other: "Credits") -> "Credits":
        return Credits(amount=self.amount + other.amount)

    def __sub__(self, other: "Credits") -> "Credits":
        if self.amount < other.amount:
            raise ValueError("Credit balance cannot become negative")
        return Credits(amount=self.amount - other.amount)

class Reputation(BaseModel):
    score: float = Field(ge=0.0, le=100.0, default=100.0)

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        return max(0.0, min(100.0, round(float(v), 2)))
