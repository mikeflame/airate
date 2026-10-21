"""Reference solution for tasks/py-config-validate, standard library only; proves the task is solvable."""

MODES = {"fast", "safe"}
TOP_KEYS = {"name", "port", "mode", "tags", "retries", "db"}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_port(value, path, errors):
    if not _is_int(value) or not 1 <= value <= 65535:
        errors.append(f"{path} must be an integer from 1 to 65535")


def _check_db(db, errors):
    if not isinstance(db, dict):
        errors.append("db must be an object")
        return
    host = db.get("host")
    if not isinstance(host, str) or not host:
        errors.append("db.host must be a non-empty string")
    if "port" not in db:
        errors.append("db.port is required")
    else:
        _check_port(db["port"], "db.port", errors)


def validate_config(config):
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["$ must be an object"]
    for key in config:
        if key not in TOP_KEYS:
            errors.append(f"{key} is not an allowed key")
    name = config.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name must be a non-empty string")
    if "port" not in config:
        errors.append("port is required")
    else:
        _check_port(config["port"], "port", errors)
    mode = config.get("mode")
    if mode not in MODES:
        errors.append("mode must be 'fast' or 'safe'")
    if "tags" in config:
        tags = config["tags"]
        if not isinstance(tags, list):
            errors.append("tags must be a list")
        else:
            for i, tag in enumerate(tags):
                if not isinstance(tag, str) or not tag:
                    errors.append(f"tags[{i}] must be a non-empty string")
            if len(set(t for t in tags if isinstance(t, str))) != len(tags):
                errors.append("tags must be unique")
    if "retries" in config:
        if not _is_int(config["retries"]) or not 0 <= config["retries"] <= 10:
            errors.append("retries must be an integer from 0 to 10")
    if "db" in config:
        _check_db(config["db"], errors)
    elif mode == "safe":
        errors.append("db is required when mode is safe")
    return errors
