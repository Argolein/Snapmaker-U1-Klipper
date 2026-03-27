DEFAULT_ACTION = 'cancel'
DEFAULT_MESSAGE = 'class CodedException'
DEFAULT_ID = 522
DEFAULT_INDEX = 0
DEFAULT_CODE = 0
DEFAULT_ONESHOT = 1
DEFAULT_LEVEL = 3
DEFAULT_PERSISTENT = 0
DEFAULT_PROACTIVE_REPORT = 1

class CodedException(Exception):
    default_action = DEFAULT_ACTION
    default_id = DEFAULT_ID
    default_index = DEFAULT_INDEX
    default_code = DEFAULT_CODE
    default_message = DEFAULT_MESSAGE
    default_oneshot = DEFAULT_ONESHOT
    default_level = DEFAULT_LEVEL
    default_persistent = DEFAULT_PERSISTENT
    default_proactive_report = DEFAULT_PROACTIVE_REPORT

    def __init__(
        self,
        message: str = default_message,
        action: str = default_action,
        id: int = default_id,
        index: int = default_index,
        code: int = default_code,
        oneshot: int = default_oneshot,
        level: int = default_level,
        is_persistent: int = default_persistent,
        proactive_report: int = default_proactive_report,
    ):
        self.id = id
        self.index = index
        self.code = code
        self.oneshot = oneshot
        self.message = message
        self.action = action
        self.level = level
        self.is_persistent = is_persistent
        self.proactive_report = proactive_report

        super().__init__(message)


    def to_dict(self) -> dict:
        # Dynamically generate dictionary from instance attributes
        return {key: getattr(self, key) for key in self.__dict__ if not key.startswith("_")}

    def structured_code(self) -> str:
        return f"{self.level:04d}-{self.id:04d}-{self.index:04d}-{self.code:04d}"

    def basic_structured_code(self) -> str:
        return f"{self.id:04d}-{self.index:04d}-{self.code:04d}"

    @classmethod
    def from_exception(cls, exc: Exception, **kwargs):
        if not isinstance(exc, Exception):
            raise TypeError("Input must be an instance of Exception")

        if isinstance(exc, cls):
            for key, value in kwargs.items():
                if hasattr(exc, key):
                    setattr(exc, key, value)
            return exc

        new_exc = cls(
            message=kwargs.get("message", str(exc)),
            action=kwargs.get("action", cls.default_action),
            id=kwargs.get("id", cls.default_id),
            index=kwargs.get("index", cls.default_index),
            code=kwargs.get("code", cls.default_code),
            oneshot=kwargs.get("oneshot", cls.default_oneshot),
            level=kwargs.get("level", cls.default_level),
            is_persistent=kwargs.get("is_persistent", cls.default_persistent),
            proactive_report=kwargs.get("proactive_report", cls.default_proactive_report),
        )
        new_exc.__cause__ = exc
        return new_exc
