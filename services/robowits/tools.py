"""Robot observation, absolute Cartesian action and calibrated pixel geometry tools."""
import math
import numpy as np
from . import tasks as TK
from services.simulator.io import RejectedBeforeAction, reject_unless
CAMERAS = TK.CAMERAS
MAX_ACTION_ROWS = 100
FRAME_STRIDE = TK.FRAME_STRIDE
STATUS, OBSERVE, STEP, PIXEL_TO_POSITION = ('robowits_status','robowits_observe','robowits_step','robowits_pixel_to_position')


def action_layout():
    return {'dim':14, 'frame':'robot_base', 'control_mode':'EE_ABS', 'fields':[
        {'slice':[0,3],'name':'right_ee_position_m'}, {'slice':[3,6],'name':'left_ee_position_m'},
        {'slice':[6,9],'name':'right_ee_axis_angle_rad'}, {'slice':[9,12],'name':'left_ee_axis_angle_rad'},
        {'slice':[12,13],'name':'right_gripper_width_m'}, {'slice':[13,14],'name':'left_gripper_width_m'}],
        'semantics':'Absolute targets, one native physics/control step per row. Gripper 0 closes, 0.08 opens. Repeat targets to converge. Hold unused arm at its observed pose.'}


def state_layout():
    return dict(action_layout(), semantics='Measured proprioception with the same ordering as actions')


def tool_definitions():
    empty={'type':'object','properties':{},'additionalProperties':False}
    return [
      {'name':STATUS,'description':'Robot state, episode status and action layout. No physics.','inputSchema':empty},
      {'name':OBSERVE,'description':'Current 848x480 ego and two wrist RGB images, calibrated depth when enabled, and 14D robot-base EE proprioception. No physics.','inputSchema':empty},
      {'name':STEP,'description':'Execute 1..100 absolute EE target rows. Order: right XYZ, left XYZ, right rotation vector, left rotation vector, right and left gripper widths (m). Robot-base frame; grippers 0 closes and 0.08 opens. Each row advances one ~33.3ms step. Stops on native terminal state. Use repeated rows to allow the joints to converge; reobserve near contact.',
       'inputSchema':{'type':'object','required':['actions'],'additionalProperties':False,'properties':{'actions':{'type':'array','minItems':1,'maxItems':100,'items':{'type':'array','minItems':14,'maxItems':14,'items':{'type':'number'}}}}}},
      {'name':PIXEL_TO_POSITION,'description':'Back-project pixels from the current depth image into robot-base coordinates (metres). Depth observes surfaces, not hidden object centers.',
       'inputSchema':{'type':'object','required':['observation_step_id','selections'],'additionalProperties':False,'properties':{
         'observation_step_id':{'type':'integer'},'depth_window_radius':{'type':'integer','minimum':0,'maximum':5},
         'selections':{'type':'array','minItems':1,'maxItems':32,'items':{'type':'object','required':['camera','u','v'],'additionalProperties':False,'properties':{'camera':{'type':'string','enum':list(CAMERAS)},'u':{'type':'number'},'v':{'type':'number'}}}}}}}]


def _only(arguments, allowed):
    unknown = set(arguments) - set(allowed)
    reject_unless(not unknown, f"Unknown arguments: {sorted(unknown)}")


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_actions(actions):
    reject_unless(isinstance(actions, list) and 1 <= len(actions) <= MAX_ACTION_ROWS, 'actions must contain 1..100 rows')
    for row in actions:
        reject_unless(isinstance(row, list) and len(row) == 14 and all(_finite(v) for v in row), 'each row needs 14 finite numbers')
        reject_unless(all(abs(v) <= 2 for v in row[:6]), 'EE positions must be within 2m of robot base')
        reject_unless(all(abs(v) <= 2*math.pi for v in row[6:12]), 'axis-angle components must be within +/-2pi')
        reject_unless(all(0 <= v <= .1 for v in row[12:]), 'gripper width must be 0..0.1m')


def validate_request(name, arguments):
    """Complete schema/shape validation before dispatch; raises RejectedBeforeAction."""
    reject_unless(isinstance(arguments, dict), "arguments must be an object")
    if name in (STATUS, OBSERVE):
        _only(arguments, ())
    elif name == STEP:
        _only(arguments, ("actions",))
        validate_actions(arguments.get("actions"))
    elif name == PIXEL_TO_POSITION:
        _only(arguments, ("observation_step_id", "selections", "depth_window_radius"))
        reject_unless(type(arguments.get("observation_step_id")) is int, "observation_step_id must be an integer")
        selections = arguments.get("selections")
        reject_unless(isinstance(selections, list) and 1 <= len(selections) <= 32, "selections must have 1..32 items")
        for item in selections:
            reject_unless(isinstance(item, dict) and set(item) == {"camera", "u", "v"}, "selection needs camera, u, v")
            reject_unless(item["camera"] in CAMERAS, f"unknown camera {item['camera']!r}")
            reject_unless(_finite(item["u"]) and _finite(item["v"]), "pixel coordinates must be finite numbers")
        radius = arguments.get("depth_window_radius", 1)
        reject_unless(type(radius) is int and 0 <= radius <= 5, "depth_window_radius must be 0..5")
    else:
        raise RejectedBeforeAction(f"Unknown RoboWits tool: {name}")


def quaternion_matrix(q_wxyz):
    w, x, y, z = (float(v) for v in q_wxyz)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def pixel_to_world(depth_m, intrinsics, pose, u, v, radius=1):
    """Back-project one pixel of a linear-depth image into the robot-base frame.

    Genesis rasterizer cameras look down their -Z axis with +Y up (OpenGL); the images are upright (row 0 at the
    top). Depth is distance along the optical axis (linear render depth). Uses the median
    valid depth in a (2r+1)^2 window.
    """
    depth = np.asarray(depth_m, dtype=np.float64)
    height, width = depth.shape[:2]
    reject_unless(0 <= u < width and 0 <= v < height, f"pixel ({u}, {v}) outside {width}x{height}")
    iu, iv = int(round(u)), int(round(v))
    window = depth[max(0, iv - radius):iv + radius + 1, max(0, iu - radius):iu + radius + 1]
    valid = window[np.isfinite(window) & (window > 0)]
    if valid.size == 0:
        return {"valid": False, "reason": "no valid depth near the pixel"}
    z = float(np.median(valid))
    K = np.asarray(intrinsics["matrix"], dtype=np.float64)
    point_camera = np.array([(u - K[0, 2]) / K[0, 0] * z, -(v - K[1, 2]) / K[1, 1] * z, -z])
    point = quaternion_matrix(pose["quaternion_wxyz"]) @ point_camera + np.asarray(pose["position_m"], dtype=np.float64)
    return {"valid": True, "position_m": point.tolist(), "depth_m": z, "frame": "robot_base",
            "depth_spread_m": float(valid.max() - valid.min())}


def world_to_pixel(point, intrinsics, pose):
    """Project a world-frame point into a camera (inverse of pixel_to_world)."""
    local = quaternion_matrix(pose["quaternion_wxyz"]).T @ (np.asarray(point, dtype=np.float64)
                                                            - np.asarray(pose["position_m"], dtype=np.float64))
    if local[2] >= 0:
        return None
    z = -local[2]
    K = np.asarray(intrinsics["matrix"], dtype=np.float64)
    return float(K[0, 0] * local[0] / z + K[0, 2]), float(-K[1, 1] * local[1] / z + K[1, 2]), float(z)
