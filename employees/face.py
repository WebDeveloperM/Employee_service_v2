from functools import lru_cache
from io import BytesIO
import base64
import binascii
import os
import threading

import numpy as np
import requests
from PIL import Image
from django.conf import settings

try:
    import cv2
except ImportError:
    cv2 = None


# cv2.FaceDetectorYN/FaceRecognizerSF instances are cached as process-wide
# singletons (see _get_sface_engines) but their setInputSize()/detect()/
# alignCrop()/feature() calls mutate shared internal state and are not
# thread-safe. Django's runserver handles requests on multiple threads, so
# concurrent face-verify calls without this lock corrupt the shared detector
# and crash with OpenCV assertion errors (e.g. matrix_wrap.cpp FIXED_TYPE).
_FACE_ENGINE_LOCK = threading.Lock()

FACE_MODEL_DIR = os.path.join(settings.BASE_DIR, 'employees', 'ml_models')
YUNET_MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
SFACE_MODEL_FILENAME = 'face_recognition_sface_2021dec.onnx'
YUNET_MODEL_URL = 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
SFACE_MODEL_URL = 'https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx'


def _require_cv2():
    if cv2 is None:
        raise ValueError('opencv-contrib-python-headless is not installed in employee_service environment')
    return cv2


def decode_image_to_pil(image_payload: str):
    if not image_payload:
        return None

    encoded = str(image_payload)
    if ',' in encoded:
        encoded = encoded.split(',', 1)[1]

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None

    if not image_bytes:
        return None

    try:
        image = Image.open(BytesIO(image_bytes))
        return image.convert('RGB')
    except Exception:
        return None


def _ensure_face_model_file(filename: str, url: str):
    os.makedirs(FACE_MODEL_DIR, exist_ok=True)
    model_path = os.path.join(FACE_MODEL_DIR, filename)
    if os.path.exists(model_path):
        return model_path, None

    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
    except Exception as exc:
        return None, f'Юз моделини юклаб бўлмади {filename}: {exc}'

    with open(model_path, 'wb') as model_file:
        model_file.write(response.content)
    return model_path, None


@lru_cache(maxsize=1)
def _get_sface_engines():
    cv2_module = _require_cv2()
    yunet_path, yunet_error = _ensure_face_model_file(YUNET_MODEL_FILENAME, YUNET_MODEL_URL)
    if yunet_error:
        return None, None, yunet_error

    sface_path, sface_error = _ensure_face_model_file(SFACE_MODEL_FILENAME, SFACE_MODEL_URL)
    if sface_error:
        return None, None, sface_error

    try:
        detector = cv2_module.FaceDetectorYN.create(yunet_path, '', (320, 320), 0.9, 0.3, 5000)
        recognizer = cv2_module.FaceRecognizerSF.create(sface_path, '')
    except Exception as exc:
        return None, None, f'SFace ни ишга тушириб бўлмади: {exc}'

    return detector, recognizer, None


def _extract_face_embedding(image: Image.Image):
    cv2_module = _require_cv2()
    detector, recognizer, engine_error = _get_sface_engines()
    if detector is None or recognizer is None:
        return None, engine_error or 'SFace мотори мавжуд эмас'

    rgb = np.asarray(image.convert('RGB'))
    bgr = cv2_module.cvtColor(rgb, cv2_module.COLOR_RGB2BGR)
    with _FACE_ENGINE_LOCK:
        detector.setInputSize((bgr.shape[1], bgr.shape[0]))
        _, faces = detector.detect(bgr)
        if faces is None or len(faces) == 0:
            return None, 'Эмбеддинг қуриш учун юз топилмади'

        best_face = max(faces, key=lambda face: float(face[2]) * float(face[3]))
        try:
            aligned = recognizer.alignCrop(bgr, best_face)
            embedding = recognizer.feature(aligned)
        except Exception as exc:
            return None, f'Юз эмбеддингини ҳисоблаб бўлмади: {exc}'

    if embedding is None:
        return None, 'Face embedding is empty'

    return embedding.flatten().astype(np.float32), None


@lru_cache(maxsize=1)
def _get_face_cascade_classifier():
    cv2_module = _require_cv2()
    cascade_path = cv2_module.data.haarcascades + 'haarcascade_frontalface_default.xml'
    classifier = cv2_module.CascadeClassifier(cascade_path)
    if classifier.empty():
        raise ValueError('Could not load Haar cascade model')
    return classifier


def extract_primary_face(image: Image.Image):
    cv2_module = _require_cv2()
    classifier = _get_face_cascade_classifier()
    rgb = np.asarray(image.convert('RGB'))
    gray = cv2_module.cvtColor(rgb, cv2_module.COLOR_RGB2GRAY)
    faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if faces is None or len(faces) == 0:
        return None

    x, y, w, h = max(faces, key=lambda face: int(face[2]) * int(face[3]))
    padding_x = int(w * 0.12)
    padding_y = int(h * 0.18)
    x1 = max(x - padding_x, 0)
    y1 = max(y - padding_y, 0)
    x2 = min(x + w + padding_x, gray.shape[1])
    y2 = min(y + h + padding_y, gray.shape[0])
    face_gray = gray[y1:y2, x1:x2]
    if face_gray.size == 0:
        return None
    face_gray = cv2_module.resize(face_gray, (160, 160), interpolation=cv2_module.INTER_AREA)
    return face_gray


def detect_face_boxes(image: Image.Image):
    cv2_module = _require_cv2()
    rgb = np.asarray(image.convert('RGB'))
    bgr = cv2_module.cvtColor(rgb, cv2_module.COLOR_RGB2BGR)
    detector, _, _ = _get_sface_engines()
    if detector is not None:
        try:
            with _FACE_ENGINE_LOCK:
                detector.setInputSize((bgr.shape[1], bgr.shape[0]))
                _, faces = detector.detect(bgr)
            if faces is not None and len(faces) > 0:
                return [
                    {
                        'x': int(face[0]),
                        'y': int(face[1]),
                        'width': int(face[2]),
                        'height': int(face[3]),
                    }
                    for face in faces
                ]
        except Exception:
            pass

    classifier = _get_face_cascade_classifier()
    gray = cv2_module.cvtColor(rgb, cv2_module.COLOR_RGB2GRAY)
    faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
    if faces is None or len(faces) == 0:
        return []

    return [
        {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
        for x, y, w, h in faces
    ]


def estimate_head_pose_from_frames(images: list) -> dict:
    cv2_module = _require_cv2()
    detector, _, engine_error = _get_sface_engines()
    if detector is None:
        return {'error': engine_error or 'Face detector unavailable', 'poses': [], 'valid_frames': 0}

    face_3d = np.array([
        (0.0, 0.0, 0.0),
        (-43.3, 32.7, -26.0),
        (43.3, 32.7, -26.0),
        (-28.9, -28.9, -24.1),
        (28.9, -28.9, -24.1),
    ], dtype=np.float64)

    pose_results = []

    for image in images:
        if image is None:
            continue
        rgb = np.asarray(image.convert('RGB'))
        bgr = cv2_module.cvtColor(rgb, cv2_module.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]

        try:
            with _FACE_ENGINE_LOCK:
                detector.setInputSize((w, h))
                _, faces = detector.detect(bgr)
        except Exception:
            pose_results.append({'yaw': 0.0, 'pitch': 0.0, 'face_detected': False})
            continue

        if faces is None or len(faces) == 0:
            pose_results.append({'yaw': 0.0, 'pitch': 0.0, 'face_detected': False})
            continue

        best_face = max(faces, key=lambda f: float(f[2]) * float(f[3]))

        x_nt, y_nt = float(best_face[8]), float(best_face[9])
        x_re, y_re = float(best_face[4]), float(best_face[5])
        x_le, y_le = float(best_face[6]), float(best_face[7])
        x_rcm, y_rcm = float(best_face[10]), float(best_face[11])
        x_lcm, y_lcm = float(best_face[12]), float(best_face[13])

        image_2d = np.array([
            (x_nt, y_nt),
            (x_re, y_re),
            (x_le, y_le),
            (x_rcm, y_rcm),
            (x_lcm, y_lcm),
        ], dtype=np.float64)

        focal_length = float(w)
        camera_matrix = np.array([
            [focal_length, 0, w / 2.0],
            [0, focal_length, h / 2.0],
            [0, 0, 1.0],
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        try:
            success, rvec, _ = cv2_module.solvePnP(
                face_3d, image_2d, camera_matrix, dist_coeffs,
                flags=cv2_module.SOLVEPNP_ITERATIVE,
            )
            if not success:
                pose_results.append({'yaw': 0.0, 'pitch': 0.0, 'face_detected': True})
                continue

            rmat, _ = cv2_module.Rodrigues(rvec)
            sy = float(np.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2))
            if sy > 1e-6:
                pitch = float(np.degrees(np.arctan2(rmat[2, 1], rmat[2, 2])))
                yaw = float(np.degrees(np.arctan2(-rmat[2, 0], sy)))
            else:
                pitch = float(np.degrees(np.arctan2(-rmat[1, 2], rmat[1, 1])))
                yaw = float(np.degrees(np.arctan2(-rmat[2, 0], sy)))

            pose_results.append({'yaw': round(yaw, 1), 'pitch': round(pitch, 1), 'face_detected': True})
        except Exception:
            pose_results.append({'yaw': 0.0, 'pitch': 0.0, 'face_detected': True})

    valid_poses = [p for p in pose_results if p['face_detected']]
    if not valid_poses:
        return {'poses': pose_results, 'valid_frames': 0, 'yaw_range': 0.0, 'max_yaw': 0.0,
                'max_right_yaw': 0.0, 'max_left_yaw': 0.0, 'max_up_pitch': 0.0}

    yaws = [p['yaw'] for p in valid_poses]
    pitches = [p['pitch'] for p in valid_poses]

    return {
        'poses': pose_results,
        'valid_frames': len(valid_poses),
        'total_frames': len(pose_results),
        'yaw_range': round(max(yaws) - min(yaws), 1),
        'max_right_yaw': round(max(yaws), 1),
        'max_left_yaw': round(min(yaws), 1),
        'max_yaw': round(max(yaws, key=abs), 1),
        'max_up_pitch': round(min(pitches), 1),
        'max_down_pitch': round(max(pitches), 1),
    }


def _embedding_similarity_percent(reference_embedding: np.ndarray, captured_embedding: np.ndarray) -> float:
    denom = float(np.linalg.norm(reference_embedding) * np.linalg.norm(captured_embedding))
    if denom == 0:
        return 0.0
    similarity = float(np.dot(reference_embedding, captured_embedding) / denom)
    similarity = max(min(similarity, 1.0), -1.0)
    return max(0.0, ((similarity + 1.0) / 2.0) * 100.0)


def _orb_similarity(face_a: np.ndarray, face_b: np.ndarray) -> float:
    cv2_module = _require_cv2()
    orb = cv2_module.ORB_create(nfeatures=256)
    keypoints_a, descriptors_a = orb.detectAndCompute(face_a, None)
    keypoints_b, descriptors_b = orb.detectAndCompute(face_b, None)
    if not keypoints_a or not keypoints_b or descriptors_a is None or descriptors_b is None:
        return 0.0
    matcher = cv2_module.BFMatcher(cv2_module.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(descriptors_a, descriptors_b)
    if not matches:
        return 0.0
    distances = [match.distance for match in matches]
    score = max(0.0, 100.0 - (sum(distances) / len(distances)))
    return min(score, 100.0)


def _ncc_similarity(face_a: np.ndarray, face_b: np.ndarray) -> float:
    a = face_a.astype(np.float32)
    b = face_b.astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    value = float(np.sum(a * b) / denom)
    return max(0.0, ((value + 1.0) / 2.0) * 100.0)


def _gradient_similarity(face_a: np.ndarray, face_b: np.ndarray) -> float:
    grad_ax = np.diff(face_a.astype(np.float32), axis=1)
    grad_ay = np.diff(face_a.astype(np.float32), axis=0)
    grad_bx = np.diff(face_b.astype(np.float32), axis=1)
    grad_by = np.diff(face_b.astype(np.float32), axis=0)
    ax = np.concatenate([grad_ax.flatten(), grad_ay.flatten()])
    bx = np.concatenate([grad_bx.flatten(), grad_by.flatten()])
    denom = float(np.linalg.norm(ax) * np.linalg.norm(bx))
    if denom == 0:
        return 0.0
    value = float(np.dot(ax, bx) / denom)
    return max(0.0, ((value + 1.0) / 2.0) * 100.0)


def _hog_similarity(face_a: np.ndarray, face_b: np.ndarray) -> float:
    cv2_module = _require_cv2()
    win_size = (64, 64)
    hog = cv2_module.HOGDescriptor(
        win_size,
        (16, 16),
        (8, 8),
        (8, 8),
        9,
    )
    resized_a = cv2_module.resize(face_a.astype(np.uint8), win_size, interpolation=cv2_module.INTER_AREA)
    resized_b = cv2_module.resize(face_b.astype(np.uint8), win_size, interpolation=cv2_module.INTER_AREA)
    desc_a = hog.compute(resized_a)
    desc_b = hog.compute(resized_b)
    if desc_a is None or desc_b is None:
        return 0.0
    denom = float(np.linalg.norm(desc_a) * np.linalg.norm(desc_b))
    if denom == 0:
        return 0.0
    value = float(np.dot(desc_a.flatten(), desc_b.flatten()) / denom)
    return max(0.0, ((value + 1.0) / 2.0) * 100.0)


def calculate_face_similarity(reference_image: Image.Image, captured_image: Image.Image) -> float:
    cv2_module = _require_cv2()
    reference_embedding, _ = _extract_face_embedding(reference_image)
    captured_embedding, _ = _extract_face_embedding(captured_image)

    ref_face = extract_primary_face(reference_image)
    cap_face = extract_primary_face(captured_image)
    if ref_face is None or cap_face is None:
        raise ValueError('Face not detected in one of the images')

    ref_np = np.asarray(ref_face, dtype=np.float32)
    cap_np = np.asarray(cap_face, dtype=np.float32)

    pixel_diff = float(np.mean(np.abs(ref_np - cap_np)))
    pixel_similarity = max(0.0, 100.0 - (pixel_diff / 255.0) * 100.0)

    hist_ref = cv2_module.calcHist([ref_face.astype(np.uint8)], [0], None, [256], [0, 256])
    hist_cap = cv2_module.calcHist([cap_face.astype(np.uint8)], [0], None, [256], [0, 256])
    cv2_module.normalize(hist_ref, hist_ref)
    cv2_module.normalize(hist_cap, hist_cap)
    histogram_similarity = max(0.0, cv2_module.compareHist(hist_ref, hist_cap, cv2_module.HISTCMP_CORREL) * 100.0)

    orb_similarity = _orb_similarity(ref_face.astype(np.uint8), cap_face.astype(np.uint8))
    ncc_similarity = _ncc_similarity(ref_np, cap_np)
    gradient_similarity = _gradient_similarity(ref_np, cap_np)
    hog_similarity = _hog_similarity(ref_face.astype(np.uint8), cap_face.astype(np.uint8))

    classic_similarity = (
        pixel_similarity * 0.10
        + histogram_similarity * 0.07
        + orb_similarity * 0.10
        + ncc_similarity * 0.33
        + gradient_similarity * 0.22
        + hog_similarity * 0.18
    )

    if reference_embedding is not None and captured_embedding is not None:
        embedding_similarity = _embedding_similarity_percent(reference_embedding, captured_embedding)
        return (embedding_similarity * 0.80) + (classic_similarity * 0.20)

    return classic_similarity