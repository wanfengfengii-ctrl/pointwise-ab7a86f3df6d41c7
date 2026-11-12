NORMAL = [
    {"id": "capture", "parents": [], "reads": {}, "writes": {"well-a": 10, "well-b": 20}},
    {"id": "correction", "parents": ["capture"], "reads": {"well-a": 10}, "writes": {"well-a": 12}},
]

AMBIGUOUS = [
    {"id": "east-agent", "parents": [], "reads": {}, "writes": {"well-a": 10, "well-b": 20}},
    {"id": "west-agent", "parents": [], "reads": {}, "writes": {"well-a": 11, "well-b": 19}},
]

DELETE = [
    {"id": "withdraw", "parents": ["east-agent", "west-agent"],
     "reads": {}, "writes": {"well-a": None}},
]

OBSERVED = [
    {"id": "readback", "parents": ["east-agent", "west-agent"],
     "reads": {"well-a": 11, "well-b": 19}, "writes": {}},
]
