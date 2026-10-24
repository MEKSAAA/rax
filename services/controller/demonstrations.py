"""Host-side visual context for offline research workspaces, never simulator data."""
from .storage import persist


def provision_demonstration(config, workspace, demos_root, *, record=True):
    """record=False for an agent formal copy: the trial's descriptor belongs to its exploration.
    demos_root (runtime/reference-demos) holds each benchmark's demonstration cache."""
    from services.benchmarks import for_config
    context = for_config(config).provision_demonstration(config, workspace/'runtime/demonstrations', demos_root)
    # Keep the trusted descriptor separate from agent-writable image/manifest copies.
    if record:
        persist(config.root/'demonstration-context.json', context)
    return context


def task_demonstration_context(context, *, live_formal=False, bundles=True):
    """live_formal: for an agent formal copy. bundles: code mode, whose rehearsals and formal
    batch run the bundle without these images. Nothing when no demonstration is supplied."""
    if context['kind'] == 'none':
        return ''
    count = context['image_count']
    one = count == 1
    # A benchmark may supply several independent demonstrations, each in its own layout.
    demonstrations = context.get('demonstration_count', 1)
    kind = 'terminal-state' if context['kind'] == 'terminal_state' else 'completion-sequence'
    supplied = (f"one {kind} image" if one else
                f"{count} terminal-state images" if context['kind'] == 'terminal_state' else
                f"a completion sequence of {count} images" if demonstrations == 1 else
                f"{demonstrations} completion sequences, each in its own layout ({count} images in all)")
    order = ("" if context["kind"] == "terminal_state" else "; preserve their order" if demonstrations == 1 else
             ";\n`demonstrations` lists each sequence's images in order")
    return (f"\n## Visual demonstration\n\nSupplied: {supplied}.\n"
            "Read [the image manifest](runtime/demonstrations/manifest.json) and view the\n"
            + ("local image before acting. Its `ordered_images` path is relative to\n"
               "`/workspace/runtime/demonstrations/`.\n" if one else
               "local images before acting. `ordered_images` paths are relative to\n"
               f"`/workspace/runtime/demonstrations/`{order}.\n")
            + "Use the in-context-action-learning skill. "
            + ("This image explains the task goal;\nits layout may differ from the current scene. It contains no executable\n"
               "actions or simulator state." if one else
               "These images explain task goals;\ntheir layout may differ from the current scene. They contain no executable\n"
               "actions or simulator state.")
            + " Satisfy the conditions above in the current layout.\n"
            + ("" if live_formal or not bundles else
               ("Rehearsals and the formal batch do not see this image; include any reference\n" if one else
                "Rehearsals and the formal batch do not see these images; include any reference\n")
               + "image your controller needs in its bundle.\n"))
