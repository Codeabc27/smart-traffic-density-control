from BACKEND.services.signal_optimizer import SignalController

def test_min_green():
    c=SignalController()
    snap=c.update({"NORTH":30,"EAST":1,"SOUTH":1,"WEST":1},{"NORTH":1,"EAST":0,"SOUTH":0,"WEST":0},0,0)
    assert snap["state"] == "GREEN"
    assert snap["green_seconds"] >= 15

def test_fairness_priority():
    c=SignalController(phase="NORTH",state="GREEN",green_duration=60,state_started_at=0,phase_started_at=0,waiting={"NORTH":0,"EAST":50,"SOUTH":0,"WEST":0})
    snap=c.update({"NORTH":30,"EAST":1,"SOUTH":1,"WEST":1},{"NORTH":0,"EAST":0,"SOUTH":0,"WEST":0},16,16)
    assert snap["state"] == "YELLOW"
