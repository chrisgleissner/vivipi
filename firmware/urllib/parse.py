class _ParsedUrl:
    def __init__(self, scheme, hostname, port):
        self.scheme = scheme
        self.hostname = hostname
        self.port = port


def urlparse(value):
    raw = str(value).strip()
    scheme = ""
    remainder = raw
    if "://" in raw:
        scheme, remainder = raw.split("://", 1)

    authority = remainder.split("/", 1)[0]
    if not authority:
        return _ParsedUrl(scheme, "", None)

    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]

    host = authority
    port = None
    if authority.startswith("[") and "]" in authority:
        host, remainder = authority[1:].split("]", 1)
        if remainder.startswith(":") and remainder[1:].isdigit():
            port = int(remainder[1:])
    else:
        candidate_host, separator, candidate_port = authority.rpartition(":")
        if separator and candidate_host and candidate_port.isdigit():
            host = candidate_host
            port = int(candidate_port)

    return _ParsedUrl(scheme, host, port)