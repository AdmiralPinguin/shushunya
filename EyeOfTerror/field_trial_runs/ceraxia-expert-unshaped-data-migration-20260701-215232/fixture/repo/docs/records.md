# Records

Legacy records with `amount` remain readable. Writers emit `total_amount` so rollback can still read old stored data while new outputs use the new shape.
