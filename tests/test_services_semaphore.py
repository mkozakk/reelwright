from services.common import semaphore


def test_semaphore_acquire_blocks_at_cap_and_release_frees_it(aws_stack):
    table_name = aws_stack["jobs_table"]

    assert semaphore.acquire_slot(table_name, "job1", cap=1) is True
    assert semaphore.acquire_slot(table_name, "job2", cap=1) is False

    semaphore.release_slot(table_name, "job1")
    assert semaphore.acquire_slot(table_name, "job2", cap=1) is True


def test_release_on_unheld_slot_is_a_no_op(aws_stack):
    table_name = aws_stack["jobs_table"]

    semaphore.release_slot(table_name, "job1")
    assert semaphore.acquire_slot(table_name, "job1", cap=1) is True
