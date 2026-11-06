from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

PREFIXES = ["Mr.", "Mrs.", "Ms.", "Mx.", "Dr."]

# Congress.gov policy areas (used for CWC LibraryOfCongressTopic and web-form topic matching).
TOPICS = [
    "Agriculture and Food", "Armed Forces and National Security", "Civil Rights and Liberties, Minority Issues",
    "Crime and Law Enforcement", "Economics and Public Finance", "Education", "Energy",
    "Environmental Protection", "Families", "Finance and Financial Sector", "Foreign Trade and International Finance",
    "Government Operations and Politics", "Health", "Housing and Community Development", "Immigration",
    "International Affairs", "Labor and Employment", "Public Lands and Natural Resources",
    "Science, Technology, Communications", "Social Welfare", "Taxation", "Transportation and Public Works",
]


@dataclass
class Constituent:
    prefix: str
    first: str
    last: str
    email: str
    street: str
    city: str
    state: str
    zip5: str
    phone: str = ""
    street2: str = ""
    zip4: str = ""
    lat: float | None = None
    lon: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}".strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Constituent":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})

    def lob_address(self) -> dict:
        a = {
            "name": self.full_name,
            "address_line1": self.street,
            "address_city": self.city,
            "address_state": self.state,
            "address_zip": self.zip5,
        }
        if self.street2:
            a["address_line2"] = self.street2
        return a
