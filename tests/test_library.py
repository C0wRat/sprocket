from sprocket import library


def test_sanitize_roll():
    assert library.sanitize_roll("  Summer 2024 ") == "Summer 2024"
    assert library.sanitize_roll("../etc") == "etc"
    assert library.sanitize_roll("a/b\\c") == "a-b-c"
    assert library.sanitize_roll("Été à Paris") == "Été à Paris"
    assert library.sanitize_roll("..") == "roll"
    assert library.sanitize_roll("") == "roll"


def test_names_never_overwrite_and_frames_continue(tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    assert library.next_frame(tmp_path, "r") == 1
    (folder / "r-01.jpg").touch()
    (folder / "r-07_raw.tif").touch()
    (folder / "r-07-2.jpg").touch()
    (folder / "notes.txt").touch()
    assert library.unique_base(folder, "r-01") == "r-01-2"
    assert library.unique_base(folder, "r-02") == "r-02"
    assert library.next_frame(tmp_path, "r") == 8


def test_list_scans_groups_files(tmp_path):
    folder = tmp_path / "Roll A"
    (folder / library.THUMBS).mkdir(parents=True)
    (folder / "Roll A-01.jpg").write_bytes(b"jpeg")
    (folder / "Roll A-01_raw.tif").write_bytes(b"tiff!")
    (folder / library.THUMBS / "Roll A-01.jpg").write_bytes(b"thumb")
    (folder / "Roll A-02_raw.tif.part").write_bytes(b"incomplete")

    [scan] = library.list_scans(tmp_path)
    assert scan["name"] == "Roll A-01"
    assert scan["files"]["jpeg"] == {"url": "/files/Roll%20A/Roll%20A-01.jpg", "size": 4}
    assert scan["files"]["tiff"]["size"] == 5
    assert scan["thumb"] == "/files/Roll%20A/.thumbs/Roll%20A-01.jpg"
