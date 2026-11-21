"""Standard identity values for application forms, from the reviewed profile and resume only.

Nothing here guesses: a value is used only when the profile states it, or when the resume
states exactly one candidate for it (a city and country only from a "City, Country" place). Anything else is left empty for the answer bank or
the person.
"""

import re

COUNTRIES = {
    "United Kingdom": "united kingdom|uk|gb|gbr|great britain|britain|england|scotland|wales",
    "United States": "united states|united states of america|usa|us",
    "Canada": "canada",
    "Ireland": "ireland",
    "Germany": "germany|deutschland",
    "Switzerland": "switzerland",
    "France": "france",
    "Netherlands": "netherlands|the netherlands",
    "Belgium": "belgium",
    "Austria": "austria",
    "Denmark": "denmark",
    "Sweden": "sweden",
    "Spain": "spain",
    "Italy": "italy",
}
COUNTRY_NAMES = {alias: name for name, aliases in COUNTRIES.items() for alias in aliases.split("|")}

PHONE_LINE = re.compile(
    r"(?m)^\s*(?:Phone|Tel|Mobile)\s*:\s*([+\d][\d ()+.-]{6,})\s*$", re.IGNORECASE
)
# A phone number in a contact line: international or trunk prefix and 9-15 digits.
PHONE_TOKEN = re.compile(r"^(?:\+|00|0|\()[\d ()./-]+$")
CONTACT_SEPARATORS = re.compile(r"\s*(?:[·•|;]|\s,\s|,\s+(?=\+))\s*")


def country_from_place(place):
    """The country a "City, Country" place names, or "" when it names none."""
    parts = [p.strip().casefold() for p in (place or "").split(",")[1:] if p.strip()]
    return COUNTRY_NAMES.get(parts[-1], "") if parts else ""


PLACE_LINE = re.compile(r"(?m)^\s*(?:Location|Address|Based in)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _header_tokens(cv):
    """Separated items of the resume's first lines, where the contact details sit."""
    header = [line for line in cv.splitlines() if line.strip()][:8]
    for line in header:
        for token in CONTACT_SEPARATORS.split(line.strip().lstrip("#").strip()):
            yield token.strip()


def resume_phones(cv):
    """Phone numbers stated in a "Phone:" line or in the resume's contact header."""
    found = [p.strip() for p in PHONE_LINE.findall(cv)]
    for token in _header_tokens(cv):
        digits = re.sub(r"\D", "", token)
        if PHONE_TOKEN.match(token) and 9 <= len(digits) <= 15:
            found.append(token)
    return list(dict.fromkeys(found))


def resume_places(cv):
    """ "City, Country" places stated in a "Location:" line or in the resume's contact header."""
    found = [p.strip() for p in PLACE_LINE.findall(cv) if country_from_place(p)]
    for token in _header_tokens(cv):
        parts = [p.strip() for p in token.split(",")]
        short = len(token) <= 60 and len(parts) <= 4 and all(len(p.split()) <= 4 for p in parts)
        if short and country_from_place(token):
            found.append(token)
    return list(dict.fromkeys(found))


def contact_values(profile, cv=""):
    """Values for standard form fields; empty when neither the profile nor the resume states one."""
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", cv))
    phones = resume_phones(cv)
    linkedin = set(re.findall(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", cv))
    github = set(re.findall(r"(?:https?://)?github\.com/[\w-]+/?", cv))
    places = resume_places(cv)
    countries = {country_from_place(p) for p in places}
    city = profile.city or (places[0] if len(places) == 1 else "")
    names = profile.name.split()
    return {
        "first_name": profile.first_name or (names[0] if names else ""),
        "last_name": profile.last_name or " ".join(names[1:]),
        "full_name": profile.name,
        "email": profile.email or (next(iter(emails)) if len(emails) == 1 else ""),
        "phone": profile.phone or (phones[0] if len(phones) == 1 else ""),
        "city": city,
        "country": country_from_place(profile.city)
        or (next(iter(countries)) if len(countries) == 1 and not profile.city else ""),
        "linkedin": profile.linkedin or (next(iter(linkedin)) if len(linkedin) == 1 else ""),
        "github": profile.github or (next(iter(github)) if len(github) == 1 else ""),
        "website": profile.website,
    }
