import boto3
import pytest

from services.common import dynamo, s3keys, storage


def test_s3keys_are_stable_and_sortable():
    assert s3keys.raw_key("job1", "src1") == "raw/job1/src1"
    assert s3keys.work_audio_key("job1", "src1") == "work/job1/audio/src1.flac"
    assert s3keys.work_clip_key("job1", 2) == "work/job1/clips/002.mp4"
    assert s3keys.work_clip_key("job1", 12) > s3keys.work_clip_key("job1", 2)
    assert s3keys.output_key("job1") == "output/job1/montage.mp4"
    assert s3keys.thumbnail_key("job1") == "output/job1/thumbnail.jpg"


def test_new_analysis_s3keys_match_the_data_model_table_shapes():
    assert s3keys.work_loudness_key("job1", "src1") == "work/job1/loudness/src1.json"
    assert s3keys.work_scenes_key("job1", "src1") == "work/job1/scenes/src1.json"
    assert s3keys.work_transcript_key("job1", "src1") == "work/job1/transcript/src1.json"


def test_set_analysis_key_survives_two_writes_to_different_categories_on_the_same_item(aws_stack):
    # two "concurrent" Analyze branches writing different analysis_keys
    # leaves must not clobber each other on the same item
    table_name = aws_stack["jobs_table"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    table.put_item(Item={"pk": dynamo.job_pk("job1"), "status": "ANALYZING"})

    dynamo.set_analysis_key(table_name, "job1", "loudness", {"src1": "work/job1/loudness/src1.json"})
    dynamo.set_analysis_key(table_name, "job1", "scenes", {"src1": "work/job1/scenes/src1.json"})

    job = dynamo.get_job(table_name, "job1")
    assert job.analysis_keys == {
        "loudness": {"src1": "work/job1/loudness/src1.json"},
        "scenes": {"src1": "work/job1/scenes/src1.json"},
    }


def test_set_analysis_key_survives_regardless_of_write_order(aws_stack):
    table_name = aws_stack["jobs_table"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    table.put_item(Item={"pk": dynamo.job_pk("job2"), "status": "ANALYZING"})

    dynamo.set_analysis_key(table_name, "job2", "scenes", {"src1": "work/job2/scenes/src1.json"})
    dynamo.set_analysis_key(table_name, "job2", "loudness", {"src1": "work/job2/loudness/src1.json"})

    job = dynamo.get_job(table_name, "job2")
    assert job.analysis_keys == {
        "scenes": {"src1": "work/job2/scenes/src1.json"},
        "loudness": {"src1": "work/job2/loudness/src1.json"},
    }


def test_set_analysis_key_works_when_analysis_keys_attribute_is_entirely_absent(aws_stack):
    # if_not_exists path -- must not require a prior writer to have already
    # created the analysis_keys map
    table_name = aws_stack["jobs_table"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    table.put_item(Item={"pk": dynamo.job_pk("job3"), "status": "ANALYZING"})

    dynamo.set_analysis_key(table_name, "job3", "audio", {"src1": "work/job3/audio/src1.flac"})

    job = dynamo.get_job(table_name, "job3")
    assert job.analysis_keys == {"audio": {"src1": "work/job3/audio/src1.flac"}}


def test_conditional_status_flip_absorbs_duplicate_calls(aws_stack):
    table_name = aws_stack["jobs_table"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    table.put_item(Item={"pk": dynamo.job_pk("job1"), "status": "UPLOADING"})

    assert dynamo.conditional_status_flip(table_name, "job1", "UPLOADING", "ANALYZING") is True
    assert dynamo.get_job(table_name, "job1").status == "ANALYZING"

    assert dynamo.conditional_status_flip(table_name, "job1", "UPLOADING", "ANALYZING") is False
    assert dynamo.get_job(table_name, "job1").status == "ANALYZING"


def test_update_job_and_mark_failed(aws_stack):
    table_name = aws_stack["jobs_table"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    table.put_item(Item={"pk": dynamo.job_pk("job1"), "status": "RENDERING"})

    dynamo.update_job(table_name, "job1", output_key="output/job1/montage.mp4")
    job = dynamo.get_job(table_name, "job1")
    assert job.output_key == "output/job1/montage.mp4"

    dynamo.mark_failed(table_name, "job1", "boom")
    job = dynamo.get_job(table_name, "job1")
    assert job.status == "FAILED"
    assert job.error == "boom"


def test_get_job_missing_raises(aws_stack):
    with pytest.raises(KeyError):
        dynamo.get_job(aws_stack["jobs_table"], "does-not-exist")


def test_storage_round_trips_a_file(aws_stack, tmp_path):
    bucket = aws_stack["raw_bucket"]
    local_in = tmp_path / "hello.txt"
    local_in.write_text("hello")

    storage.upload(bucket, "raw/job1/hello.txt", local_in)
    assert storage.list_keys(bucket, "raw/job1/") == ["raw/job1/hello.txt"]

    dest_dir = tmp_path / "download"
    downloaded = storage.download(bucket, "raw/job1/hello.txt", dest_dir)
    assert downloaded.read_text() == "hello"
