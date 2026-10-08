from platespinner.traymodel import build


def plate(name, state, reason=None, wait=0, snooze_left=0, muted=False, branch="main", sid=None):
    return {"session_id": sid or name, "name": name, "branch": branch, "state": state, "reason": reason,
            "wait": wait, "snooze_left": snooze_left, "muted": muted}


PRESETS = [300, 900, 3600]


def test_all_spinning_is_grey_without_count():
    m = build({"plates": [plate("infra", "spinning", wait=40)], "paused_left": 0}, PRESETS, 900)
    assert (m.icon, m.label) == ("platespinner-idle", "")
    row = m.rows[0]
    assert row.text == "-  infra  main  spinning 40s"
    assert [a.label for a in row.actions] == ["Jump"]


def test_wobbling_rows_get_amber_count_and_snooze_presets():
    m = build({"plates": [plate("api", "wobbling", "permission", wait=420),
                          plate("docs", "wobbling", "done", wait=120)], "paused_left": 0}, PRESETS, 900)
    assert (m.icon, m.label) == ("platespinner-wobble", "2")
    assert m.rows[0].text == "!  api  main  permission 7m"
    assert [a.label for a in m.rows[0].actions] == ["Jump", "Snooze 5m", "Snooze 15m", "Snooze 1h", "Mute"]
    assert m.rows[0].actions[2].cmd == {"cmd": "snooze", "target": "api", "duration": 900}


def test_snoozed_row_offers_wake():
    m = build({"plates": [plate("data", "wobbling", "done", wait=60, snooze_left=720)], "paused_left": 0},
              PRESETS, 900)
    assert m.rows[0].text == "z  data  main  snoozed, 12m left"
    assert [a.label for a in m.rows[0].actions] == ["Jump", "Wake"]
    assert m.label == ""


def test_muted_row():
    m = build({"plates": [plate("x", "wobbling", "done", wait=60, muted=True)], "paused_left": 0}, PRESETS, 900)
    assert m.rows[0].text == "m  x  main  muted, done 1m"
    assert [a.label for a in m.rows[0].actions] == ["Jump", "Wake"]


def test_footer_and_pause_state():
    m = build({"plates": [], "paused_left": 0}, PRESETS, 900)
    assert [a.label for a in m.footer] == ["Snooze all 15m", "Pause 1h", "Quit"]
    m = build({"plates": [], "paused_left": 1800}, PRESETS, 900)
    assert [a.label for a in m.footer] == ["Snooze all 15m", "Resume (paused, 30m left)", "Quit"]
    assert m.footer[1].cmd == {"cmd": "pause", "duration": 0}
