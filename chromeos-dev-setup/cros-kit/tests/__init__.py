from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "cros_kit"


def runtime_functions():
    """Source runtime functions using the runner's declared fixture tools.

    The deployed script must use ChromeOS's fixed tool paths. Nix tests instead
    receive their tools through PATH; preserve that path only in this harness.
    The original file remains the input to syntax and payload checks.
    """
    source = (SOURCE / "rw-init.sh").read_text()
    entrypoint = '\nrw_main "$@"'
    tool_path = "PATH=/sbin:/bin:/usr/sbin:/usr/bin"
    if source.count(entrypoint) != 1 or source.count(tool_path) != 1:
        raise ValueError("Runtime fixture adapter no longer matches rw-init.sh")
    return source.rsplit(entrypoint, 1)[0].replace(
        tool_path, "# Keep the test runner's fixture tool PATH.", 1)
