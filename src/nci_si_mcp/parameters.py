"""What a tool parameter means, stated next to the parameter in the handler signature.

The core package has no dependencies, so a signature cannot carry pydantic's `Field`. It carries
`Described` metadata in `Annotated[...]` instead; `server.py` turns it into the description and
the schema keywords of the served input schema, and the CLI ignores it. The nested records of an
argument (TypedDicts) are described with `describe_fields`, which the server adds to the schema.
"""

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

# The stated forms of the identifiers that go into an upstream path or query (spec/tools.yaml).
TERMINOLOGY_FORM = "^[a-z][a-z0-9_]*$"
RELEASE_FORM = "^[A-Za-z0-9][A-Za-z0-9._-]*$"
NCIT_CODE_FORM = "^C[1-9][0-9]*$"
REGISTRY_ID_FORM = "^[1-9][0-9]*$"
REGISTRY_VERSION_FORM = "^[0-9]+([.][0-9]+)?$"


@dataclass(frozen=True)
class Described:
    """The plain-word description of a parameter and the constraints the schema states."""

    description: str
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None

    def field_arguments(self) -> dict[str, Any]:
        """The arguments of pydantic's `Field` that state this."""

        stated = {
            "description": self.description,
            "pattern": self.pattern,
            "min_length": self.min_items,
            "max_length": self.max_items,
        }
        return {key: value for key, value in stated.items() if value is not None}


def count_bound(what: str, default: int, maximum: int) -> Described:
    """A count the tool bounds: its default and maximum, which the schema does not enforce."""

    return Described(
        f"{what} Default {default}, at most {maximum}; a larger value is applied as {maximum}."
    )


# Nested records of the arguments, by TypedDict name: the description of each of its fields.
FIELD_DESCRIPTIONS: dict[str, dict[str, str]] = {}


def describe_fields(record: type, **descriptions: str) -> None:
    """State what each field of the TypedDict `record` means; every field needs a text."""

    if set(descriptions) != set(record.__annotations__):
        raise TypeError(f"{record.__name__} needs one description for each of its fields")
    FIELD_DESCRIPTIONS[record.__name__] = descriptions


Terminology = Annotated[
    str,
    Described(
        "Short lowercase name of the terminology, for example ncit.", pattern=TERMINOLOGY_FORM
    ),
]
Code = Annotated[
    str,
    Described("Code of the concept in that terminology, for example C3262 for NCIt."),
]
Release = Annotated[
    str | None,
    Described(
        "Release of the terminology to read, for example 26.06e; resolve_release names the "
        "current one. For NCIt, leave it unset to use the current release of the configured "
        "channel, kept for the MCP session. Other terminologies need it.",
        pattern=RELEASE_FORM,
    ),
]
NcitRelease = Annotated[
    str | None,
    Described(
        "NCIt release to use, for example 26.06e. Leave it unset to use the current release "
        "of the configured channel, kept for the MCP session.",
        pattern=RELEASE_FORM,
    ),
]
RegistryRelease = Annotated[
    str | None,
    Described(
        "caDSR publishes no registry-level release yet (C-1); leave unset. Once one exists "
        "it becomes mandatory and a mismatch fails closed, as release does for NCIt."
    ),
]
Cursor = Annotated[
    str | None,
    Described(
        "The nextCursor of the previous page, passed back unchanged to get the next page. "
        "Leave unset for the first page."
    ),
]


# The filters of the three data-element matching tools share one type. A tool refuses the keys its
# upstream route does not serve (search_data_elements takes no classificationScheme).
class SchemeFilter(TypedDict):
    publicId: str
    version: str


describe_fields(
    SchemeFilter,
    publicId="Public id of the classification scheme, for example 2200604.",
    version="Version of the classification scheme, for example 1.0.",
)


class MatchFilters(TypedDict, total=False):
    context: str
    workflowStatus: str
    registrationStatus: str
    valueDomainType: str
    classificationScheme: SchemeFilter


describe_fields(
    MatchFilters,
    context="Only data elements of this caDSR context, for example NCIP.",
    workflowStatus="Only data elements with this workflow status, for example RELEASED.",
    registrationStatus="Only data elements with this registration status, for example Standard.",
    valueDomainType="Only data elements with this value domain type, for example Enumerated.",
    classificationScheme="Only data elements in this classification scheme; give both its "
    "publicId and version.",
)
