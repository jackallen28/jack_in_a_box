"""AirPrint: IPP codec, raster formats, documents -> receipt pages, the IPP server, Bonjour records, settings."""
import http.client
import io
import socket
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import airprint as ap  # noqa: E402
from app import ipp  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.rasterfmt import RasterError, read_pwg, read_urf  # noqa: E402
from app.service import Service  # noqa: E402


# ----------------------------------------------------------------------------- helpers: pictures, documents, a pretend phone
def jpeg(size=(600, 800), draw=True):
    im = Image.new("RGB", size, "white")
    if draw:
        d = ImageDraw.Draw(im)
        d.rectangle([60, 60, size[0] - 60, size[1] - 60], outline="black", width=8)
        d.ellipse([150, 150, size[0] - 150, size[1] - 150], fill="black")
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


def png(size=(600, 800), color="white", box=None):
    im = Image.new("RGB", size, color)
    if box:
        ImageDraw.Draw(im).rectangle(box, fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def pdf(pages=2, size=(850, 1100)):
    ims = []
    for i in range(pages):
        im = Image.new("RGB", size, "white")
        d = ImageDraw.Draw(im)
        d.rectangle([100, 100 + i * 40, size[0] - 100, 300 + i * 40], fill="black")
        ims.append(im)
    b = io.BytesIO()
    ims[0].save(b, "PDF", save_all=True, append_images=ims[1:], resolution=100)
    return b.getvalue()


def packbits_line(row: bytes, unit: int) -> bytes:
    """Encode one line of pixels (``unit`` bytes each) with the URF/PWG scheme, runs and literals."""
    px = [row[i:i + unit] for i in range(0, len(row), unit)]
    out, i = bytearray(), 0
    while i < len(px):
        j = i
        while j + 1 < len(px) and px[j + 1] == px[i] and j - i < 127:
            j += 1
        if j > i:
            out += bytes([j - i]) + px[i]
            i = j + 1
        else:
            k = i
            while k + 1 < len(px) and px[k + 1] != px[k] and k - i < 127:
                k += 1
            lit = px[i:k + 1]
            out += bytes([257 - len(lit)]) + b"".join(lit)
            i = k + 1
    return bytes(out)


def encode_rows(im: Image.Image, unit: int) -> bytes:
    raw = im.tobytes()
    stride = im.width * unit
    rows = [raw[y * stride:(y + 1) * stride] for y in range(im.height)]
    out, y = bytearray(), 0
    while y < len(rows):
        n = 1
        while y + n < len(rows) and rows[y + n] == rows[y] and n < 256:
            n += 1
        out += bytes([n - 1]) + packbits_line(rows[y], unit)
        y += n
    return bytes(out)


def urf(images, bpp=8):
    out = bytearray(b"UNIRAST\x00" + struct.pack(">I", len(images)))
    for im in images:
        im = im.convert("L" if bpp == 8 else "RGB")
        out += bytes([bpp, 1 if bpp == 8 else 0, 1, 4]) + b"\x00" * 8 + struct.pack(">III", im.width, im.height, 300) + b"\x00" * 8
        out += encode_rows(im, bpp // 8)
    return bytes(out)


def pwg(images):
    out = bytearray(b"RaS2")
    for im in images:
        im = im.convert("L")
        h = bytearray(1796)
        struct.pack_into(">II", h, 276, 300, 300)
        struct.pack_into(">II", h, 372, im.width, im.height)
        struct.pack_into(">II", h, 384, 8, 8)
        struct.pack_into(">I", h, 392, im.width)
        out += bytes(h) + encode_rows(im, 1)
    return bytes(out)


def request(op, op_attrs=(), job_attrs=(), doc=b"", rid=7, version=(2, 0)):
    ops = [("attributes-charset", ipp.charset("utf-8")), ("attributes-natural-language", ipp.lang("en")),
           ("printer-uri", ipp.uri("ipp://printer/ipp/print")), *op_attrs]
    groups = [(ipp.G_OPERATION, ops)]
    if job_attrs:
        groups.append((ipp.G_JOB, list(job_attrs)))
    return ipp.build_response(op, rid, groups, version, doc)


def print_job(doc, fmt="application/octet-stream", name="My document", copies=None, **kw):
    return request(ipp.PRINT_JOB, [("requesting-user-name", ipp.name("jack")), ("job-name", ipp.name(name)), ("document-format", ipp.mime(fmt))],
                   [("copies", ipp.integer(copies))] if copies else [], doc, **kw)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "zeroconf_available", lambda: False)          # no real multicast in tests
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["airprint"].update(enabled=True, port=0)
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    server = ap.AirPrintService(cfg, svc)
    server.start_server(server.settings())
    yield cfg, svc, server
    server.shutdown()


def post(server, body, path="/ipp/print", headers=None, chunked=False):
    c = http.client.HTTPConnection("127.0.0.1", server._httpd.server_address[1], timeout=10)
    h = {"Content-Type": "application/ipp", **(headers or {})}
    if chunked:
        parts = [body[i:i + 5000] for i in range(0, len(body), 5000)]
        c.putrequest("POST", path)
        c.putheader("Content-Type", h["Content-Type"])
        c.putheader("Transfer-Encoding", "chunked")
        c.putheader("Expect", "100-continue")
        c.endheaders()
        for p in parts:
            c.send(b"%x\r\n" % len(p) + p + b"\r\n")
        c.send(b"0\r\n\r\n")
    else:
        c.request("POST", path, body, h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def ask(server, body, **kw):
    status, data = post(server, body, **kw)
    assert status == 200
    return ipp.parse_request(data)                        # a response has the same layout as a request


def jobs(svc):
    return svc.db.all("SELECT id, status, title, kind, source, png, error FROM jobs WHERE source='airprint' ORDER BY id")


# ----------------------------------------------------------------------------- the IPP codec
def test_attributes_round_trip_including_collections_ranges_and_resolutions():
    attrs = [("printer-name", ipp.name("Box")), ("copies-supported", ipp.rng(1, 5)), ("printer-resolution-default", ipp.resolution(300, 300)),
             ("color-supported", ipp.boolean(False)), ("operations-supported", ipp.enum(2, 4, 11)), ("document-format-supported", ipp.mime("a/b", "c/d")),
             ("media-col-default", ipp.coll(media_size=ipp.coll(x_dimension=ipp.integer(10000), y_dimension=ipp.rng(3000, 9000)),
                                            media_source=ipp.kw("main", "tray")))]
    r = ipp.parse_request(ipp.build_response(ipp.OK, 99, [(ipp.G_PRINTER, attrs)]))
    assert r.operation == ipp.OK and r.request_id == 99 and r.version == (2, 0)
    a = r.attrs(ipp.G_PRINTER)
    assert a["printer-name"] == [(ipp.NAME, "Box")] and a["copies-supported"][0][1] == ipp.Rng(1, 5)
    assert a["printer-resolution-default"][0][1] == ipp.Res(300, 300, 3) and a["color-supported"] == [(ipp.BOOLEAN, False)]
    assert [v for _, v in a["operations-supported"]] == [2, 4, 11] and [v for _, v in a["document-format-supported"]] == ["a/b", "c/d"]
    col = ipp.collection_to_dict(a["media-col-default"][0][1])
    assert col["media-size"]["x-dimension"] == 10000 and col["media-size"]["y-dimension"] == ipp.Rng(3000, 9000) and col["media-source"] == "main"


def test_a_phone_style_request_with_a_media_col_and_a_document_parses():
    media = ipp.coll(media_size=ipp.coll(x_dimension=ipp.integer(10000), y_dimension=ipp.integer(15000)), media_top_margin=ipp.integer(0))
    r = ipp.parse_request(request(ipp.PRINT_JOB, [("document-format", ipp.mime("application/pdf"))], [("media-col", media), ("copies", ipp.integer(2))], b"%PDF-1.4 data"))
    assert r.operation == ipp.PRINT_JOB and r.get(ipp.G_OPERATION, "document-format") == "application/pdf" and r.get(ipp.G_JOB, "copies") == 2
    assert ipp.collection_to_dict(r.attrs(ipp.G_JOB)["media-col"][0][1])["media-size"]["y-dimension"] == 15000
    assert r.document == b"%PDF-1.4 data"


@pytest.mark.parametrize("data", [b"", b"\x02\x00\x00\x0b", b"\x02\x00\x00\x0b\x00\x00\x00\x01\x01", b"\x02\x00\x00\x0b\x00\x00\x00\x01\x01\x47\x00\x03abc\x00\x05utf-8",
                                  b"\x02\x00\x00\x0b\x00\x00\x00\x01\x47\x00\x00\x00\x05utf-8\x03",
                                  b"\x02\x00\x00\x0b\x00\x00\x00\x01\x01" + b"\x34\x00\x01a\x00\x00" + b"\x4a\x00\x00\x00\x01m" * 3])
def test_malformed_ipp_is_rejected_cleanly(data):
    with pytest.raises(ipp.IPPError):
        ipp.parse_request(data)


def test_collections_nested_too_deep_are_refused():
    inner = b"\x37\x00\x00\x00\x00"
    deep = b"\x01" + b"".join(b"\x34\x00\x01a\x00\x00" + b"\x4a\x00\x00\x00\x01m" for _ in range(10)) + inner * 10 + b"\x03"
    with pytest.raises(ipp.IPPError):
        ipp.parse_request(b"\x02\x00\x00\x02\x00\x00\x00\x01" + deep)


# ----------------------------------------------------------------------------- raster formats (what iPhones/Macs send)
def stripes(w=64, h=40):
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    for y in range(0, h, 8):
        d.line([0, y, w, y], fill=(y * 6) % 256, width=2)
    d.rectangle([10, 5, 30, 20], fill=0)
    return im


def test_urf_grey_and_colour_pages_decode_exactly():
    a, b = stripes(), stripes(48, 30)
    pages = read_urf(urf([a, b]))
    assert [p.size for p in pages] == [(64, 40), (48, 30)] and list(pages[0].getdata()) == list(a.getdata())
    rgb = Image.new("RGB", (30, 10), (200, 10, 10))
    assert read_urf(urf([rgb], bpp=24))[0].getpixel((3, 3)) == (200, 10, 10)


def test_pwg_raster_pages_decode_exactly():
    pages = read_pwg(pwg([stripes(), stripes(20, 20)]))
    assert [p.size for p in pages] == [(64, 40), (20, 20)] and list(pages[0].getdata()) == list(stripes().getdata())


def test_the_white_fill_code_and_repeated_lines_are_understood():
    body = b"UNIRAST\x00" + struct.pack(">I", 1) + bytes([8, 1, 1, 4]) + b"\x00" * 8 + struct.pack(">III", 10, 4, 300) + b"\x00" * 8
    body += bytes([3, 128])                                           # four identical white lines: "rest of the line is white"
    im = read_urf(body)[0]
    assert im.size == (10, 4) and set(im.getdata()) == {255}


@pytest.mark.parametrize("make", [lambda: urf([stripes()])[:-30], lambda: pwg([stripes()])[:-30], lambda: b"UNIRAST\x00\x00\x00\x00\x01" + b"\x08" * 10])
def test_truncated_raster_data_is_an_error_not_a_crash(make):
    with pytest.raises(RasterError):
        (read_urf if make().startswith(b"UNI") else read_pwg)(make())


def test_absurd_page_sizes_are_refused():
    body = b"UNIRAST\x00" + struct.pack(">I", 1) + bytes([8, 1, 1, 4]) + b"\x00" * 8 + struct.pack(">III", 60000, 60000, 300) + b"\x00" * 8
    with pytest.raises(RasterError):
        read_urf(body)


# ----------------------------------------------------------------------------- documents -> receipt pages
ST = ap.Settings()


def test_the_document_type_is_recognised_from_its_bytes_not_just_what_the_phone_says():
    assert ap.sniff(pdf(1), "application/octet-stream") == "application/pdf" and ap.sniff(jpeg(), "x/y") == "image/jpeg"
    assert ap.sniff(png(), "x/y") == "image/png" and ap.sniff(urf([stripes()]), "x/y") == "image/urf"
    assert ap.sniff(pwg([stripes()]), "x/y") == "image/pwg-raster" and ap.sniff(b"hello there\nplain", "application/octet-stream") == "text/plain"


def test_a_page_becomes_a_1_bit_image_as_wide_as_the_paper():
    t = ap.ticket(Image.open(io.BytesIO(jpeg())), ST)
    assert t.mode == "1" and t.width == 576 and t.height > 100


def test_blank_pages_are_skipped_and_blank_space_is_trimmed():
    assert ap.ticket(Image.new("RGB", (800, 1000), "white"), ST) is None
    page = Image.open(io.BytesIO(png((850, 1100), box=(300, 500, 500, 560))))
    none = ap.ticket(page, ap.Settings(autocrop="none"))
    vertical = ap.ticket(page, ap.Settings(autocrop="vertical"))
    margins = ap.ticket(page, ap.Settings(autocrop="margins"))
    assert vertical.height < none.height / 3 and vertical.height < margins.height < none.height     # side margins trimmed too: the content comes out larger


def test_text_is_sharp_and_photos_are_dithered():
    grey = Image.new("RGB", (600, 600), (150, 150, 150))
    dark = lambda im: sum(1 for p in im.getdata() if p == 0)
    assert dark(ap.ticket(grey, ap.Settings(dither="text"))) == 0                                  # a flat mid-grey page: threshold -> all white
    assert dark(ap.ticket(grey, ap.Settings(dither="photo"))) > 1000                               # dithering keeps the grey as a pattern


def test_very_long_pages_are_cut_at_the_limit():
    t = ap.ticket(Image.new("L", (200, 6000), 0), ap.Settings(max_height_mm=100))
    assert t.height <= ap.mm(100) + 1


def test_pdf_pages_render_and_the_page_limit_applies():
    pages = ap.load_pages(pdf(3), "application/pdf", ST)
    assert len(pages) == 3
    assert len(ap.load_pages(pdf(3), "application/pdf", ap.Settings(max_pages=2))) == 2
    with pytest.raises(ap.DocError):
        ap.load_pages(b"%PDF-1.4 this is not a pdf", "application/pdf", ST)


def test_other_formats_load_and_nonsense_is_a_clean_error():
    assert ap.load_pages(png(), "image/png", ST)[0].size == (600, 800)
    assert len(ap.load_pages(urf([stripes(), stripes()]), "image/urf", ST)) == 2 and len(ap.load_pages(pwg([stripes()]), "image/pwg-raster", ST)) == 1
    assert ap.load_pages(b"Hello receipt\nline two", "text/plain", ST)[0].width > 100
    with pytest.raises(ap.DocError) as e:
        ap.load_pages(b"\x00\x01\x02 binary rubbish", "application/octet-stream", ST)
    assert e.value.status == ipp.FORMAT_NOT_SUPPORTED
    with pytest.raises(ap.DocError):
        ap.load_pages(b"\xff\xd8\xff garbage jpeg", "image/jpeg", ST)


# ----------------------------------------------------------------------------- the printer description
def test_printer_attributes_cover_what_airprint_clients_need():
    info = {"uuid": "11111111-2222-3333-4444-555555555555", "started": 0, "queued": 0, "admin_url": "http://pi:8080/"}
    names = {n for _, n, _ in ap.printer_attributes(ap.Settings(), "ipp://pi:631/ipp/print", info)}
    for need in ("printer-uri-supported", "uri-security-supported", "uri-authentication-supported", "printer-name", "printer-state", "printer-state-reasons",
                 "printer-is-accepting-jobs", "operations-supported", "document-format-supported", "ipp-versions-supported", "ipp-features-supported",
                 "media-supported", "media-default", "media-ready", "media-col-database", "media-col-default", "media-col-ready", "urf-supported",
                 "printer-resolution-supported", "print-quality-supported", "sides-supported", "copies-supported", "orientation-requested-supported",
                 "charset-configured", "natural-language-configured", "printer-uuid", "printer-device-id", "queued-job-count", "printer-up-time",
                 "printer-current-time", "pdl-override-supported", "job-creation-attributes-supported", "pwg-raster-document-resolution-supported"):
        assert need in names, need


def test_txt_record_has_the_keys_ios_and_macos_look_for():
    t = ap.txt_record(ap.Settings(name="Box"), {"uuid": "u-u-i-d", "admin_url": "http://pi:8080/"})
    assert t["rp"] == "ipp/print" and t["txtvers"] == "1" and t["URF"].startswith("CP1") and t["Color"] == "F" and t["UUID"] == "u-u-i-d"
    assert "image/jpeg" in t["pdl"] and "image/urf" in t["pdl"] and "image/pwg-raster" in t["pdl"] and t["adminurl"] == "http://pi:8080/"
    assert sum(len(k) + len(v) + 2 for k, v in t.items()) < 1300                                  # fits in a DNS-SD record


def test_bonjour_records_include_the_airprint_subtype_and_the_legacy_placeholder():
    pytest.importorskip("zeroconf")
    infos = ap.build_services(ap.Settings(name="Box"), 631, ["192.168.1.50"], {"uuid": "u", "admin_url": "http://pi:8080/"})
    assert [i.type for i in infos] == ["_universal._sub._ipp._tcp.local.", "_printer._tcp.local."]
    assert infos[0].name == "Box._ipp._tcp.local." and infos[0].port == 631 and infos[0].parsed_addresses() == ["192.168.1.50"]
    assert infos[0].properties[b"rp"] == b"ipp/print" and infos[1].port == 0
    assert infos[0].server == "box-airprint.local." != f"{socket.gethostname()}.local."            # avahi owns the Pi's own host name


def test_only_your_own_network_may_print():
    st = ap.Settings()
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.4.4", "127.0.0.1", "169.254.1.1", "::1", "fe80::1%eth0", "::ffff:192.168.1.7"):
        assert ap.is_allowed(ip, st), ip
    for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34", "100.64.0.1", "2001:4860:4860::8888", "nonsense"):
        assert not ap.is_allowed(ip, st), ip
    assert ap.is_allowed("8.8.8.8", ap.Settings(allow_any_network=True))


def test_settings_survive_bad_values():
    s = ap.Settings.from_cfg({"airprint": {"enabled": 1, "port": "abc", "autocrop": "weird", "dither": "??", "max_pages": 0, "name": "  My   box  "}})
    assert s.enabled is True and s.port == 631 and s.autocrop == "margins" and s.dither == "auto" and s.max_pages == 1 and s.name == "My box"


# ----------------------------------------------------------------------------- the server
def test_get_printer_attributes_over_http_with_the_host_the_client_used(rig):
    cfg, svc, server = rig
    r = ask(server, request(ipp.GET_PRINTER_ATTRIBUTES), headers={"Host": "pi.local:631"})
    assert r.operation == ipp.OK and r.request_id == 7
    a = r.attrs(ipp.G_PRINTER)
    assert a["printer-uri-supported"][0][1] == "ipp://pi.local:631/ipp/print" and a["printer-name"][0][1] == "Jack in a Box"
    assert ipp.PRINT_JOB in [v for _, v in a["operations-supported"]] and "application/pdf" in [v for _, v in a["document-format-supported"]]
    assert r.attrs(ipp.G_OPERATION)["attributes-charset"][0][1] == "utf-8"


def test_requested_attributes_filter_the_answer(rig):
    cfg, svc, server = rig
    r = ask(server, request(ipp.GET_PRINTER_ATTRIBUTES, [("requested-attributes", ipp.kw("printer-name", "printer-state"))]))
    assert sorted(r.attrs(ipp.G_PRINTER)) == ["printer-name", "printer-state"]
    r = ask(server, request(ipp.GET_PRINTER_ATTRIBUTES, [("requested-attributes", ipp.kw("job-template"))]))
    names = set(r.attrs(ipp.G_PRINTER))
    assert "copies-supported" in names and "printer-name" not in names


def test_chunked_uploads_with_expect_100_continue_work(rig):
    cfg, svc, server = rig
    r = ask(server, print_job(jpeg((1200, 1600))), chunked=True)
    assert r.operation == ipp.OK and r.get(ipp.G_JOB, "job-id") == 1 and len(jobs(svc)) == 1


def test_http_basics(rig):
    cfg, svc, server = rig
    assert post(server, request(ipp.GET_PRINTER_ATTRIBUTES), path="/nope")[0] == 404
    assert post(server, request(ipp.GET_PRINTER_ATTRIBUTES), headers={"Content-Type": "text/plain"})[0] == 415
    c = http.client.HTTPConnection("127.0.0.1", server._httpd.server_address[1])
    c.request("GET", "/")
    r = c.getresponse()
    assert r.status == 200 and b"AirPrint" in r.read()
    c.request("GET", "/other")
    assert c.getresponse().status == 404


def test_devices_outside_your_network_are_refused(rig, monkeypatch):
    cfg, svc, server = rig
    monkeypatch.setattr(ap, "is_allowed", lambda ip, st: False)
    assert post(server, print_job(jpeg()))[0] == 403 and jobs(svc) == []


def test_oversized_bodies_are_refused(rig):
    cfg, svc, server = rig
    d = cfg.data
    d["airprint"]["max_job_mb"] = 1
    cfg.replace(d)
    try:
        assert post(server, print_job(b"\xff\xd8\xff" + b"0" * (3 * 1024 * 1024)))[0] == 413
    except (BrokenPipeError, ConnectionError):
        pass                                                      # the server hung up on the oversized upload: also correct
    assert jobs(svc) == []


def test_garbage_gets_a_proper_ipp_error(rig):
    cfg, svc, server = rig
    r = ask(server, b"\x02\x00\x00\x0b\x00\x00\x00\x01\x01\x47")
    assert r.operation == ipp.BAD_REQUEST
    assert ask(server, request(0x7FFF)).operation == ipp.OPERATION_NOT_SUPPORTED
    assert ask(server, request(ipp.GET_PRINTER_ATTRIBUTES, version=(3, 0))).operation == ipp.VERSION_NOT_SUPPORTED


# ----------------------------------------------------------------------------- printing
def test_a_photo_goes_on_the_queue_as_a_receipt_wide_picture(rig):
    cfg, svc, server = rig
    r = ask(server, print_job(jpeg(), "image/jpeg", name="Holiday snap"))
    assert r.operation == ipp.OK and r.get(ipp.G_JOB, "job-state") in (ipp.STATE_PENDING if hasattr(ipp, "STATE_PENDING") else 3, 5)
    j = jobs(svc)
    assert len(j) == 1 and j[0]["status"] == "pending" and j[0]["title"] == "AirPrint: Holiday snap" and j[0]["kind"] == "airprint"
    im = Image.open(svc.hist_dir / j[0]["png"])
    assert im.mode == "1" and im.width == 576
    svc.process_pending()
    assert jobs(svc)[0]["status"] == "printed"
    done = ask(server, request(ipp.GET_JOB_ATTRIBUTES, [("job-id", ipp.integer(1))]))
    assert done.get(ipp.G_JOB, "job-state") == 9 and done.get(ipp.G_JOB, "job-impressions-completed") == 1


def test_a_multi_page_pdf_prints_one_ticket_per_page_and_copies_multiply(rig):
    cfg, svc, server = rig
    ask(server, print_job(pdf(3), "application/pdf", name="Report", copies=2))
    titles = [j["title"] for j in jobs(svc)]
    assert len(titles) == 6 and "AirPrint: Report (2/3) copy 2" in titles


def test_urf_and_pwg_documents_print_too(rig):
    cfg, svc, server = rig
    page = Image.new("L", (400, 500), 255)
    ImageDraw.Draw(page).rectangle([50, 50, 350, 150], fill=0)
    assert ask(server, print_job(urf([page]), "image/urf")).operation == ipp.OK
    assert ask(server, print_job(pwg([page]), "image/pwg-raster")).operation == ipp.OK
    assert len(jobs(svc)) == 2


def test_a_blank_document_is_completed_without_wasting_paper(rig):
    cfg, svc, server = rig
    r = ask(server, print_job(png((800, 1000)), "image/png"))
    assert r.operation == ipp.OK and r.get(ipp.G_JOB, "job-state") == 9 and jobs(svc) == []


def test_unsupported_and_unreadable_documents_are_refused_with_the_right_codes(rig):
    cfg, svc, server = rig
    assert ask(server, print_job(b"data", "application/x-weird")).operation == ipp.FORMAT_NOT_SUPPORTED
    bad = ask(server, print_job(b"\xff\xd8\xff not really a jpeg", "image/jpeg"))
    assert bad.operation == ipp.BAD_REQUEST
    assert ask(server, print_job(b"", "image/jpeg")).operation == ipp.BAD_REQUEST
    assert jobs(svc) == []
    st = ask(server, request(ipp.GET_JOB_ATTRIBUTES, [("job-id", ipp.integer(2))]))
    assert st.get(ipp.G_JOB, "job-state") == 8                                                    # the unreadable one is "aborted"


def test_validate_job_checks_without_printing(rig):
    cfg, svc, server = rig
    assert ask(server, request(ipp.VALIDATE_JOB, [("document-format", ipp.mime("image/jpeg"))])).operation == ipp.OK
    assert ask(server, request(ipp.VALIDATE_JOB, [("document-format", ipp.mime("video/mp4"))])).operation == ipp.FORMAT_NOT_SUPPORTED
    assert jobs(svc) == []


def test_create_job_then_send_document(rig):
    cfg, svc, server = rig
    created = ask(server, request(ipp.CREATE_JOB, [("job-name", ipp.name("Two step"))]))
    jid = created.get(ipp.G_JOB, "job-id")
    assert created.get(ipp.G_JOB, "job-state") == 4 and jobs(svc) == []
    sent = ask(server, request(ipp.SEND_DOCUMENT, [("job-id", ipp.integer(jid)), ("document-format", ipp.mime("image/jpeg")), ("last-document", ipp.boolean(True))], doc=jpeg()))
    assert sent.operation == ipp.OK and len(jobs(svc)) == 1
    assert ask(server, request(ipp.SEND_DOCUMENT, [("job-id", ipp.integer(999)), ("document-format", ipp.mime("image/jpeg"))], doc=jpeg())).operation == ipp.NOT_FOUND


def test_hold_for_button_makes_them_wait_like_friends_messages(rig):
    cfg, svc, server = rig
    d = cfg.data
    d["airprint"]["hold_for_button"] = True
    cfg.replace(d)
    r = ask(server, print_job(jpeg(), "image/jpeg"))
    assert r.get(ipp.G_JOB, "job-state") == 4 and jobs(svc)[0]["status"] == "held"
    assert svc.release_held() == 1
    svc.process_pending()
    assert jobs(svc)[0]["status"] == "printed"


def test_cancel_job_removes_waiting_pages(rig):
    cfg, svc, server = rig
    ask(server, print_job(pdf(2), "application/pdf"))
    assert ask(server, request(ipp.CANCEL_JOB, [("job-id", ipp.integer(1))])).operation == ipp.OK
    assert [(j["status"], j["error"]) for j in jobs(svc)] == [("failed", "Cancelled")] * 2
    assert ask(server, request(ipp.GET_JOB_ATTRIBUTES, [("job-id", ipp.integer(1))])).get(ipp.G_JOB, "job-state") == 7
    assert ask(server, request(ipp.CANCEL_JOB, [("job-id", ipp.integer(1))])).operation == ipp.NOT_POSSIBLE
    assert ask(server, request(ipp.CANCEL_JOB, [("job-id", ipp.integer(42))])).operation == ipp.NOT_FOUND


def test_get_jobs_lists_waiting_and_finished_separately(rig):
    cfg, svc, server = rig
    ask(server, print_job(jpeg(), "image/jpeg", name="first"))
    ask(server, print_job(jpeg(), "image/jpeg", name="second"))
    svc.process_pending()
    ask(server, print_job(jpeg(), "image/jpeg", name="third"))

    def names(which):
        r = ask(server, request(ipp.GET_JOBS, [("which-jobs", ipp.keyword(which)) if hasattr(ipp, "keyword") else ("which-jobs", ipp.kw(which))]))
        return [d["job-name"][0][1] for tag, d in r.groups if tag == ipp.G_JOB]
    assert names("not-completed") == ["third"] and names("completed") == ["second", "first"]


def test_job_uri_can_be_used_instead_of_job_id(rig):
    cfg, svc, server = rig
    r = ask(server, print_job(jpeg(), "image/jpeg"), headers={"Host": "pi:631"})
    uri = r.get(ipp.G_JOB, "job-uri")
    assert uri == "ipp://pi:631/ipp/print/1"
    got = ask(server, request(ipp.GET_JOB_ATTRIBUTES, [("job-uri", ipp.uri(uri))]))
    assert got.get(ipp.G_JOB, "job-id") == 1


def test_a_full_queue_says_busy_and_stops_advertising_that_it_accepts_jobs(rig):
    cfg, svc, server = rig
    d = cfg.data
    d["airprint"]["max_queued"] = 2
    cfg.replace(d)
    ask(server, print_job(pdf(2), "application/pdf"))
    assert ask(server, print_job(jpeg(), "image/jpeg")).operation == ipp.BUSY
    accepting = ask(server, request(ipp.GET_PRINTER_ATTRIBUTES, [("requested-attributes", ipp.kw("printer-is-accepting-jobs", "queued-job-count"))]))
    assert accepting.get(ipp.G_PRINTER, "printer-is-accepting-jobs") is False and accepting.get(ipp.G_PRINTER, "queued-job-count") == 2


def test_airprint_pages_appear_in_the_normal_history_with_their_source(rig):
    cfg, svc, server = rig
    ask(server, print_job(jpeg(), "image/jpeg", name="Shopping list"))
    row = svc.db.one("SELECT source, kind, title FROM jobs")
    assert row == {"source": "airprint", "kind": "airprint", "title": "AirPrint: Shopping list"}


# ----------------------------------------------------------------------------- start / stop / ports
def test_it_starts_stops_and_renames_from_the_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "zeroconf_available", lambda: False)
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["airprint"].update(enabled=False, port=0)
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    s = ap.AirPrintService(cfg, svc)
    s.reconcile()
    assert s._httpd is None and s.status["state"] == "disabled"
    d = cfg.data
    d["airprint"]["enabled"] = True
    cfg.replace(d)
    s.reconcile()
    try:
        assert s._httpd is not None and s.status["state"] == "ok" and s.status["url"].startswith("ipp://") and s.status["port"] > 0
        assert "not announced" in s.status["message"] and any("zeroconf" in w for w in s.status["warnings"])
        first = s._httpd
        d = cfg.data
        d["airprint"]["name"] = "Kitchen printer"
        cfg.replace(d)
        s.reconcile()
        assert s._httpd is not first and s.status["name"] == "Kitchen printer"
        d = cfg.data
        d["airprint"]["enabled"] = False
        cfg.replace(d)
        s.reconcile()
        assert s._httpd is None and s.status["state"] == "disabled"
    finally:
        s.shutdown()


def test_port_631_falls_back_to_6310_when_it_cannot_be_used(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "zeroconf_available", lambda: False)
    real = ap.ThreadingHTTPServer
    tried = []

    def fake(addr, handler):
        tried.append(addr[1])
        if addr[1] == 631:
            raise PermissionError(13, "Permission denied")
        return real(("0.0.0.0", 0), handler)
    monkeypatch.setattr(ap, "ThreadingHTTPServer", fake)
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["airprint"].update(enabled=True, port=631)
    cfg.replace(d)
    s = ap.AirPrintService(cfg, Service(cfg, tmp_path))
    s.reconcile()
    try:
        assert tried == [631, 6310] and s.status["state"] == "ok" and any("6310" in w for w in s.status["warnings"])
    finally:
        s.shutdown()


def test_a_port_that_cannot_be_used_is_reported_not_retried_every_few_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "zeroconf_available", lambda: False)
    blocker = socket.socket()
    blocker.bind(("0.0.0.0", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["airprint"].update(enabled=True, port=port)
    cfg.replace(d)
    s = ap.AirPrintService(cfg, Service(cfg, tmp_path))
    try:
        s.reconcile()
        assert s.status["state"] == "error" and str(port) in s.status["message"] and s._httpd is None
        s.status["message"] = "marker"
        s.reconcile()                                                  # same settings: no second attempt
        assert s.status["message"] == "marker"
    finally:
        blocker.close()


def test_self_test_asks_our_own_port_like_a_phone_would(rig):
    cfg, svc, server = rig
    r = server.self_test()
    assert r["ok"] is True and "answers on port" in r["message"]
    server.shutdown()
    assert server.self_test()["ok"] is False


def test_addresses_skip_loopback_and_the_direct_printer_cable():
    got = ap.lan_addresses("192.168.192.168/24")
    assert "127.0.0.1" not in got and not any(a.startswith("192.168.192.") for a in got)


# ----------------------------------------------------------------------------- the web API
def test_web_api_reports_status_and_runs_the_self_test(rig):
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    cfg, svc, server = rig
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc), airprint=server).test_client()
    h = {"X-Requested-With": "fetch"}
    c.post("/api/setup", json={"password": "hunter22"}, headers=h)
    ask(server, print_job(jpeg(), "image/jpeg", name="From a phone"))
    s = c.get("/api/airprint").get_json()
    assert s["state"] == "ok" and s["pdf"] is True and "image/urf" in s["formats"] and s["recent"][0]["title"] == "AirPrint: From a phone"
    assert c.get("/api/status").get_json()["airprint"]["state"] == "ok"
    assert c.post("/api/airprint-test", headers=h).get_json()["ok"] is True
    schema = c.get("/api/schema").get_json()
    assert any(f["path"] == "airprint.enabled" for f in schema["sections"]["airprint"])


def test_a_separate_device_really_discovers_it_over_mdns():
    """Real multicast DNS: announce it, then browse for it the way an iPhone/Mac (the AirPrint subtype) and Windows/Android (plain
    _ipp._tcp) do, after the announcements are over.  Skipped where multicast isn't available."""
    zc_mod = pytest.importorskip("zeroconf")
    import time
    found: dict = {}

    class Listener:
        def __init__(self, t):
            self.t = t

        def add_service(self, zc, type_, name):
            found.setdefault(self.t, []).append(name)

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass
    b = ap.Bonjour()
    zc2 = None
    try:
        b.start(ap.Settings(name="Discovery Test Box"), 6310, ["192.0.2.2"], {"uuid": "u", "admin_url": "http://pi:8080/"})
        time.sleep(3)                                                   # a device that looks later, not during the announcements
        zc2 = zc_mod.Zeroconf(ip_version=zc_mod.IPVersion.V4Only)
        browsers = [zc_mod.ServiceBrowser(zc2, t, Listener(t)) for t in ("_ipp._tcp.local.", "_universal._sub._ipp._tcp.local.")]  # noqa: F841
        for _ in range(60):
            if len(found) == 2:
                break
            time.sleep(0.25)
    except OSError as e:
        pytest.skip(f"multicast not available here: {e}")
    finally:
        if zc2 is not None:
            zc2.close()
        b.stop()
    if not found:
        pytest.skip("no mDNS traffic in this environment")
    assert found.get("_ipp._tcp.local.") == ["Discovery Test Box._ipp._tcp.local."]
    assert found.get("_universal._sub._ipp._tcp.local.") == ["Discovery Test Box._ipp._tcp.local."]
