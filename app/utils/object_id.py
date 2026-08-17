from typing import Any

from bson import ObjectId
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic_core import CoreSchema, core_schema


class PyObjectId(ObjectId):
    """
    MongoDB ObjectId support for Pydantic v2.
    """

    @classmethod
    def validate_object_id(cls, value: str) -> ObjectId:
        if not ObjectId.is_valid(value):
            raise ValueError("Invalid ObjectId")

        return ObjectId(value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        string_to_object_id = core_schema.chain_schema(
            [
                core_schema.str_schema(),
                core_schema.no_info_plain_validator_function(
                    cls.validate_object_id
                ),
            ]
        )

        return core_schema.json_or_python_schema(
            json_schema=string_to_object_id,
            python_schema=core_schema.union_schema(
                [
                    core_schema.is_instance_schema(ObjectId),
                    string_to_object_id,
                ]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: str(value)
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema_value: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        return {
            "type": "string",
            "example": "507f1f77bcf86cd799439011",
        }