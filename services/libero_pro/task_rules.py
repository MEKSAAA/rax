"""TASK.md text for LIBERO-PRO tasks: the official instruction, the episode and success.

``public`` (the only rules level) gives what the public evaluation gives a policy: the instruction,
read from the BDDL of the collection the agent plays, and nothing about the success check. It warns
that objects can be swapped (the swap collection), so a controller must not rely on where objects
usually are; which collection a formal batch plays is never named.
"""

from services.libero_pro.tasks import (CAMERAS, CONTROL_HZ, EXPLORATION_COLLECTION, SWAP_EXPLORATION_COLLECTION,
                                       instruction, native_step_limit,
                                       task_timeouts)

TASK_RULES = ("public",)
LOCATE = ("Locate every object from the current observations in every episode; do not rely on where objects "
          "usually are.")


def task_context(task, *, task_rules="public", live_formal=False, evaluation=True, collection=EXPLORATION_COLLECTION):
    """live_formal: for an agent formal copy (collection: its formal collection, whose instruction it
    is given). evaluation: False in live-copy mode."""
    if task_rules not in TASK_RULES:
        raise ValueError(f"LIBERO-PRO task_rules must be one of {', '.join(TASK_RULES)}")
    limit = native_step_limit(task)
    minutes = task_timeouts(task)["episode_seconds"] // 60
    lines = [f"# Task: {task}", "", f"**Instruction:** {instruction(task, collection)}", "",
             "## Episode", "",
             "- Robot: one Franka Panda arm with a parallel-jaw gripper, at a table.",
             f"- Cameras: `{CAMERAS[0]}` (third-person) and `{CAMERAS[1]}` (on the gripper).",
             f"- Step limit: {limit} control steps of {1000 // CONTROL_HZ} ms each; the episode ends there or as soon "
             "as the instruction is accomplished."]
    if not live_formal:  # A formal copy reads its wall limit from `status` (FORMAL_AGENTS.md).
        lines.append(f"- Wall-clock limit per episode: about {minutes} minutes, including simulator startup.")
    lines.append("- Object placement changes between episodes.")
    if live_formal:
        lines.append("- This layout is new to you; there is no reset.")
    if evaluation and not live_formal and collection == SWAP_EXPLORATION_COLLECTION:
        # Optional second stage: exploration itself plays swapped layouts.
        lines += ["- Exploration episodes use their own layouts; formal episodes use held-out layouts, which "
                  "exploration never uses.",
                  "- In this environment objects are swapped, in exploration and formal alike: an object can start "
                  "where another object usually stands, or on top of another object. " + LOCATE]
    elif evaluation and not live_formal:
        lines += ["- Exploration episodes use their own layouts; formal episodes use held-out layouts, which "
                  "exploration never uses.",
                  "- Formal layouts may swap objects: an object can start where another object usually stands, or "
                  "on top of another object, in places exploration never shows. " + LOCATE]
    else:  # Live-copy mode frames nothing as an evaluation.
        lines.append("- Objects may be swapped in layouts you have not seen: an object can start where another "
                     "object usually stands, or on top of another object. " + LOCATE)
    lines += ["", "## Success", "",
              "- An episode succeeds or fails. There is no partial credit: an episode that ends without the "
              "instruction accomplished, hits the step limit or errors does not succeed.", ""]
    return "\n".join(lines)
