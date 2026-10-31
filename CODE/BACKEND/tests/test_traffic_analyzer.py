from BACKEND.services.traffic_analyzer import density_from_count, assign_approach

def test_density_thresholds():
    assert density_from_count(0) == "LOW"
    assert density_from_count(8) == "LOW"
    assert density_from_count(19) == "MEDIUM"
    assert density_from_count(30) == "HIGH"

def test_approach_assignment():
    assert assign_approach(320, 80, 640, 480) == "NORTH"
    assert assign_approach(560, 240, 640, 480) == "EAST"
    assert assign_approach(320, 430, 640, 480) == "SOUTH"
    assert assign_approach(80, 240, 640, 480) == "WEST"
