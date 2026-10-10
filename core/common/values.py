from api.common.schema import StorageKind

class ValueStorageLookup:
    """Resolve declared Canonical identities; identifiers have no naming semantics."""

    def __init__(self, values):
        if values is None:
            raise TypeError('Canonical value declarations are required')
        self.storage_by_id = {name: value['storage'] for name, value in values.items()}

    def storage_of(self, name):
        if name not in self.storage_by_id:
            raise ValueError(f'Undeclared Canonical value: {name!r}')
        return self.storage_by_id[name]

    def is_register(self, name):
        return self.storage_of(name) == StorageKind.REGISTER

    def is_predicate(self, name):
        return self.storage_of(name) == StorageKind.PREDICATE_REGISTER

    def is_renameable(self, name):
        return self.storage_of(name) in (StorageKind.REGISTER, StorageKind.PREDICATE_REGISTER)

    def is_ub(self, name):
        return self.storage_of(name) == StorageKind.UB

    def is_scalar(self, name):
        return self.storage_of(name) == StorageKind.SCALAR
