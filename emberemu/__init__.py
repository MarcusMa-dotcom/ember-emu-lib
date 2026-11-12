"""emberemu: Ember+ for Python.

Serve a tree of nodes and parameters as a provider, or read and control the
tree of any Ember+ device as a consumer. Pure Python, no dependencies.

    from emberemu import Provider, Consumer, Access

    provider = Provider(port=9000)
    audio = provider.add_node("Audio")
    gain = audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0,
                               access=Access.READ_WRITE)
    provider.start()

    with Consumer("127.0.0.1", 9000) as consumer:
        print(consumer.get("Audio/Gain").value)
        consumer.set_value("Audio/Gain", -6.0)
"""

from .ber import BerError
from .consumer import Consumer, ConsumerError, InvocationError
from .glow import (Access, CommandType, ConnectionDisposition, ConnectionOperation, FieldFlags,
                   GlowError, MatrixAddressingMode, MatrixType, ParameterType, StreamFormat)
from .model import (Element, Function, Matrix, Node, Parameter, Tree, check_identifier,
                    element_from_dict, format_path, parse_path)
from .provider import Client, Provider, ProviderError

__version__ = "0.1.0"

__all__ = [
    "Access", "BerError", "Client", "CommandType", "ConnectionDisposition", "ConnectionOperation",
    "Consumer", "ConsumerError", "Element", "FieldFlags", "Function", "GlowError",
    "InvocationError", "Matrix", "MatrixAddressingMode", "MatrixType", "Node", "Parameter",
    "ParameterType", "Provider", "ProviderError", "StreamFormat", "Tree", "check_identifier",
    "element_from_dict", "format_path", "parse_path", "__version__",
]
