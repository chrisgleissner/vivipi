__all__ = ["__version__"]

try:
	from importlib.metadata import PackageNotFoundError, version as _package_version
except ImportError:
	__version__ = "0.0.0"
else:
	try:
		__version__ = _package_version("vivipi")
	except PackageNotFoundError:
		__version__ = "0.0.0"
