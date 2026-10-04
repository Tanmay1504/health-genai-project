"""
data/synthetic_names.py — fake Indian-context names and places.

Synthea produces US-style patients ("Jane123 Doe456"). For the Indian-claims
framing, the fallback generator and the fraud planter draw from these lists.
Every combination is random and fictional; any resemblance to a real person
or facility is coincidental.
"""
from __future__ import annotations

import random

GIVEN_F = ["Aarti", "Priya", "Sunita", "Kavya", "Meera", "Ananya", "Lakshmi", "Pooja", "Neha", "Divya",
           "Fatima", "Simran", "Rekha", "Ishita", "Nandini", "Shreya", "Geeta", "Zoya", "Bhavna", "Revathi"]
GIVEN_M = ["Arjun", "Rahul", "Vikram", "Suresh", "Imran", "Karthik", "Amit", "Rohan", "Manoj", "Sanjay",
           "Harpreet", "Aditya", "Naveen", "Farhan", "Gopal", "Rajesh", "Siddharth", "Anil", "Deepak", "Vivek"]
FAMILY = ["Sharma", "Iyer", "Patel", "Reddy", "Khan", "Nair", "Gupta", "Singh", "Das", "Mehta", "Joshi",
          "Kulkarni", "Menon", "Chatterjee", "Pillai", "Verma", "Bose", "Rao", "Shaikh", "Gill", "Naidu", "Mishra"]

CITIES = [("Pune", "Maharashtra"), ("Bengaluru", "Karnataka"), ("Chennai", "Tamil Nadu"),
          ("Hyderabad", "Telangana"), ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh"),
          ("Kochi", "Kerala"), ("Ahmedabad", "Gujarat"), ("Kolkata", "West Bengal"), ("Indore", "Madhya Pradesh")]

STREETS = ["MG Road", "Station Road", "Gandhi Nagar", "Nehru Colony", "Lake View Layout", "Temple Street",
           "Civil Lines", "Park Avenue", "Market Road", "Shanti Vihar", "Green Park", "Rajaji Street"]

FACILITY_PREFIX = ["Sunrise", "Lotus Valley", "Neem Tree", "Bluebell", "Indus Crest", "Banyan Grove",
                   "Silver Oak", "Peepal", "Coral Bay", "Monsoon Ridge", "Tulsi Garden", "Kesar Leaf",
                   "Amaltas", "Gulmohar", "Saffron Hills"]


def person(rng: random.Random, gender: str | None = None) -> tuple[str, str, str]:
    """Return (given, family, gender)."""
    gender = gender or rng.choice(["female", "male"])
    given = rng.choice(GIVEN_F if gender == "female" else GIVEN_M)
    return given, rng.choice(FAMILY), gender


def phone(rng: random.Random) -> str:
    # 10-digit mobile starting 9/8/7, deliberately invalid-looking prefix "99999" avoided.
    return f"{rng.choice('987')}{rng.randint(100000000, 999999999)}"


def street(rng: random.Random) -> str:
    return f"{rng.randint(1, 250)}, {rng.choice(STREETS)}"
