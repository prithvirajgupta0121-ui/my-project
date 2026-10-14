"""Unit tests for the CV pipeline components."""
from collections import Counter

import numpy as np

from app.cv.detectors import ClassicalCVDetector, Det, DetectContext, YoloOnnxDetector, build_detector
from app.cv.events import EventEngine, FrameState
from app.cv.geo import VisualOdometry, gsd_m_per_px, haversine_m, offset_latlon, point_in_polygon, valid_gps
from app.cv.preprocess import PreprocessConfig, assess_quality, preprocess
from app.cv.scene import SCENE_CLASSES, classify_scene, scene_metrics
from app.cv.synthetic import SyntheticWorld
from app.cv.tracking import SortLiteTracker, build_tracker

WORLD = SyntheticWorld()


def test_preprocess_resizes_and_scores():
    frame, _ = WORLD.render(10)
    big = np.kron(frame, np.ones((2, 2, 1), np.uint8))
    r = preprocess(big, PreprocessConfig(max_width=960))
    assert r.frame.shape[1] == 960 and "resize" in r.ops
    assert 0 <= r.quality["score"] <= 100


def test_quality_detects_degradation():
    good, _ = WORLD.render(10)
    haze, _ = WORLD.render(10, degrade="haze")
    black = np.zeros_like(good)
    assert assess_quality(good)["score"] > assess_quality(haze)["score"]
    assert assess_quality(black)["score"] == 0


def test_low_light_and_dehaze_ops():
    dark, _ = WORLD.render(10, degrade="dark")
    r = preprocess(dark, PreprocessConfig(low_light=True))
    assert "low_light" in r.ops and r.frame.mean() > dark.mean()
    haze, _ = WORLD.render(10, degrade="haze")
    assert "dehaze" in preprocess(haze, PreprocessConfig(dehaze=True)).ops


def test_classical_detector_finds_vehicles():
    det = ClassicalCVDetector()
    hits = total = 0
    for t in (8, 14, 28, 35):
        f, meta = WORLD.render(t)
        dets = det.detect(preprocess(f, PreprocessConfig()).frame, DetectContext(gsd_m=0.1))
        truth = [x for x in meta["truth"] if 20 < x["x"] < 940 and 20 < x["y"] < 520 and x["kind"] == "VEHICLE"]
        total += len(truth)
        for tr in truth:
            hits += any(abs(d.cx - tr["x"]) < 25 and abs(d.cy - tr["y"]) < 25 for d in dets)
    assert total > 0 and hits / total >= 0.7


def test_detector_respects_threshold_and_classes():
    f, _ = WORLD.render(14)
    pr = preprocess(f, PreprocessConfig()).frame
    dets = ClassicalCVDetector().detect(pr, DetectContext(gsd_m=0.1, conf_threshold=0.99))
    assert dets == []
    dets = ClassicalCVDetector().detect(pr, DetectContext(gsd_m=0.1, enabled_classes=["BOAT"]))
    assert all(d.cls == "BOAT" for d in dets)


def test_model_unavailable_fallback():
    det, warn = build_detector("yolo_onnx", "/nonexistent/model.onnx")
    assert isinstance(det, ClassicalCVDetector) and "MODEL UNAVAILABLE" in warn


def test_yolo_output_parsing_v8():
    """YOLO-compatible parsing without a real model: (1, 84, N) tensor."""
    y = YoloOnnxDetector.__new__(YoloOnnxDetector)
    from app.cv.detectors import COCO_NAMES, COCO_TO_AEGIS
    y.names, y.class_map, y.input_size = COCO_NAMES, COCO_TO_AEGIS, 640
    out = np.zeros((1, 84, 3), np.float32)
    out[0, :4, 0] = [100, 100, 40, 20]; out[0, 4 + 2, 0] = 0.9     # car
    out[0, :4, 1] = [300, 200, 10, 20]; out[0, 4 + 0, 1] = 0.8     # person
    out[0, :4, 2] = [500, 500, 30, 30]; out[0, 4 + 2, 2] = 0.1     # below threshold
    dets = y.parse_output(out, 1.0, (640, 640, 3), DetectContext(conf_threshold=0.4))
    assert Counter(d.cls for d in dets) == Counter({"VEHICLE": 1, "PERSON": 1})
    car = [d for d in dets if d.cls == "VEHICLE"][0]
    assert abs(car.x - 80) < 1e-3 and abs(car.w - 40) < 1e-3


def _det(x, y, cls="VEHICLE", c=0.8):
    return Det(cls, c, x, y, 40, 20)


def test_tracker_maintains_identity_and_confirms():
    trk = SortLiteTracker(min_hits=3, max_age=3)
    ids = []
    for i in range(8):
        u = trk.update([_det(100 + 10 * i, 100), _det(500, 300 - 8 * i, "PERSON")], i, i * 0.1, (0, 0))
        ids.append(tuple(u.assignments))
    assert ids[0] == (None, None)  # tentative
    assert ids[-1][0] is not None and ids[-1][0] != ids[-1][1]
    assert len({a[0] for a in ids[3:]}) == 1  # stable identity
    u = None
    for i in range(8, 14):
        u = trk.update([], i, i * 0.1, (0, 0))
        if u.lost:
            break
    assert u and len(u.lost) == 2


def test_tracker_camera_motion_compensation():
    trk = SortLiteTracker(min_hits=2)
    last = None
    for i in range(6):  # object static on ground, camera moving 30 px/frame
        u = trk.update([_det(400 - 30 * i, 200)], i, i * 0.1, (30.0 * i, 0))
        last = u
    assert last.assignments[0] is not None and len(trk.tracks) == 1


def test_tracker_future_adapters_fall_back():
    t, warn = build_tracker("bytetrack")
    assert isinstance(t, SortLiteTracker) and "bytetrack" in warn


def _fs(i, **kw):
    base = dict(frame_index=i, t=i * 0.1, quality={"score": 80}, drone=(12.5, -35.0), tracks=[], track_geo={},
                newly_confirmed=[], lost=[], gsd=0.1)
    base.update(kw)
    return FrameState(**base)


def test_event_low_quality_and_interruption():
    eng = EventEngine()
    evs = []
    for i in range(5):
        evs += eng.process(_fs(i, quality={"score": 20}))
    evs += eng.process(_fs(6, black=True, quality={"score": 0}))
    types = Counter(e.type for e in evs)
    assert types["LOW_IMAGE_QUALITY"] == 1
    assert types["VIDEO_INTERRUPTION"] == 1


def test_event_zone_entry_exit_and_crowd():
    from app.cv.tracking import TrackState
    zone = {"name": "Z", "polygon": [[12.5, -35.0], [12.5, -34.99], [12.49, -34.99], [12.49, -35.0]]}
    eng = EventEngine(zones=[zone])
    persons = [TrackState(tid=i, cls_votes=Counter({"PERSON": 1}), x=0, y=0, w=5, h=5, confirmed=True, conf_sum=0.7, hits=1) for i in range(6)]
    geo_out = {p.tid: (12.6, -34.995) for p in persons}
    geo_in = {p.tid: (12.495, -34.995) for p in persons}
    evs = eng.process(_fs(1, tracks=persons, track_geo=geo_out))
    evs += eng.process(_fs(2, tracks=persons, track_geo=geo_in))
    evs += eng.process(_fs(3, tracks=persons, track_geo=geo_out))
    c = Counter(e.type for e in evs)
    assert c["MONITORING_ZONE_ENTRY"] == 6 and c["MONITORING_ZONE_EXIT"] == 6
    assert c["CROWD_DENSITY_CHANGE"] == 1


def test_event_config_disables_rule():
    eng = EventEngine({"enabled": {"LOW_IMAGE_QUALITY": False}})
    evs = [e for i in range(6) for e in eng.process(_fs(i, quality={"score": 10}))]
    assert not any(e.type == "LOW_IMAGE_QUALITY" for e in evs)


def test_scene_classification():
    f, _ = WORLD.render(55)  # over the urban block
    cls, conf, probs = classify_scene(scene_metrics(f))
    assert cls in SCENE_CLASSES and 0 < conf <= 1 and abs(sum(probs.values()) - 1) < 0.01
    assert cls == "URBAN"


def test_geo_helpers():
    assert abs(gsd_m_per_px(53.3, 960) - 0.1) < 0.005
    lat, lon = offset_latlon(12.5, -35.0, 100, 0)
    assert abs(haversine_m(12.5, -35.0, lat, lon) - 100) < 0.5
    assert point_in_polygon(0.5, 0.5, [[0, 0], [0, 1], [1, 1], [1, 0]])
    assert not point_in_polygon(1.5, 0.5, [[0, 0], [0, 1], [1, 1], [1, 0]])
    assert not valid_gps(0, 0) and not valid_gps(95, 0) and valid_gps(12.5, -35)


def test_visual_odometry_tracks_camera():
    import cv2
    vo = VisualOdometry()
    for i in range(10):
        f, _ = WORLD.render(10 + i * 0.2)
        g = cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (480, 270))
        vo.update(g)
    true_dx = (WORLD.drone_position(10 + 9 * 0.2)[0] - WORLD.drone_position(10)[0])[0] / 2
    assert abs(vo.cam_x - true_dx) < 6
