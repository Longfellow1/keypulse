def run_hud(*args, **kwargs):
    # AppKit is imported lazily: the view-model, controller and renderer work (and are tested) without it.
    from penpal.hud.app import run_hud as _run_hud

    return _run_hud(*args, **kwargs)


__all__ = ["run_hud"]
