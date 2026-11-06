#!/usr/bin/env python3
"""TeCo: baixa listas públicas e gera índices por CNPJ (pedaços de JSON) para o site.

Uso:
  python scripts/build_dados.py sancoes                 # baixa CEIS, CNEP e CEPIM (Portal da Transparência)
  python scripts/build_dados.py sancoes --zip ceis.zip --zip cnep.zip --zip cepim.zip   # usa arquivos locais
  python scripts/build_dados.py pgfn                    # baixa os .zip listados em scripts/pgfn_urls.txt
  python scripts/build_dados.py pgfn --zip arquivo.zip  # usa arquivos locais

Saída: dados/sancoes/<3 primeiros dígitos>.json, dados/sancoes/meta.json (e o mesmo para dados/pgfn).
"""
import argparse, csv, datetime as dt, io, json, pathlib, re, sys, tempfile, unicodedata, urllib.error, urllib.request, zipfile

csv.field_size_limit(1 << 24)
OUT = pathlib.Path("dados")
UA = {"User-Agent": "Mozilla/5.0 (compatible; TeCo/1.0)"}
BASE = "https://portaldatransparencia.gov.br/download-de-dados/{k}/{d:%Y%m%d}"
REG = re.compile(r"NEGOCI|PARCEL|GARANT|SUSPEN|DISCUT")  # heurística: situações "regularizadas" ou em discussão


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def digits(s):
    return re.sub(r"\D", "", s or "")


def download(url, dest):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        with open(dest, "rb") as f:
            return f.read(2) == b"PK"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  falhou: {url} ({e})")
        return False


def csv_rows(zpath):
    """Gera (função de índice de coluna, linha) para cada linha de cada .csv do zip."""
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as fh:
                rd = csv.reader(io.TextIOWrapper(fh, encoding="latin-1", newline=""), delimiter=";")
                hdr = [norm(h) for h in next(rd)]
                ix = lambda *k, hdr=hdr: next((i for i, h in enumerate(hdr) if all(x in h for x in k)), None)
                for row in rd:
                    yield ix, row


def cell(row, i):
    return row[i].strip() if i is not None and i < len(row) else ""


def parse_br(s):
    try:
        return dt.datetime.strptime(s.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def write_shards(data, folder, meta):
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*.json"):
        f.unlink()
    shards = {}
    for cnpj, v in data.items():
        shards.setdefault(cnpj[:3], {})[cnpj] = v
    for k, d in shards.items():
        (folder / f"{k}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta["n"] = len(data)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"{folder}: {len(data)} CNPJs em {len(shards)} arquivos")


def build_sancoes(zips):
    today, data, datas = dt.date.today(), {}, []
    for cad, zpath in zips:
        n = 0
        for ix, row in csv_rows(zpath):
            cn = digits(cell(row, ix("CNPJ")))
            if len(cn) != 14:
                continue
            cat = cell(row, ix("CATEGORIA")) or cell(row, ix("TIPO", "SANCAO")) or cell(row, ix("MOTIVO"))
            org = cell(row, ix("ORGAO", "SANCIONADOR")) or cell(row, ix("ORGAO"))
            ini, fim = cell(row, ix("INICIO")), cell(row, ix("FINAL")) or cell(row, ix("FIM"))
            f = parse_br(fim)
            vig = True if cad == "CEPIM" else (f is None or f >= today)
            data.setdefault(cn, []).append({"c": cad, "t": cat, "o": org, "i": ini, "f": fim, "v": vig})
            n += 1
        print(f"  {cad}: {n} registros de CNPJ")
    write_shards(data, OUT / "sancoes", {"data": today.isoformat(), "fonte": "Portal da Transparência (CGU): CEIS, CNEP, CEPIM"})


def build_pgfn(zips):
    agg = {}
    for zpath in zips:
        for ix, row in csv_rows(zpath):
            cn = digits(cell(row, ix("CNPJ")))
            if len(cn) != 14:
                continue
            td = ix("TIPO", "DEVEDOR")
            if td is not None and "PRINCIPAL" not in norm(cell(row, td)):
                continue  # só devedor principal, como orienta a PGFN
            v = cell(row, ix("VALOR", "CONSOLIDADO")).replace(".", "").replace(",", ".")
            try:
                v = float(v)
            except ValueError:
                continue
            a = agg.setdefault(cn, [0.0, 0.0, 0])
            a[0] += v
            a[2] += 1
            if not REG.search(norm(cell(row, ix("SITUACAO", "INSCRICAO")))):
                a[1] += v
    data = {k: {"t": round(a[0], 2), "i": round(a[1], 2), "n": a[2]} for k, a in agg.items()}
    write_shards(data, OUT / "pgfn", {"data": dt.date.today().isoformat(), "fonte": "PGFN: dados abertos da dívida ativa da União"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["sancoes", "pgfn"])
    ap.add_argument("--zip", action="append", default=[])
    a = ap.parse_args()
    tmp = pathlib.Path(tempfile.mkdtemp())
    if a.modo == "sancoes":
        zips = []
        if a.zip:
            for z in a.zip:
                cad = next((c for c in ("CEIS", "CNEP", "CEPIM") if c in z.upper()), None)
                if not cad:
                    sys.exit(f"Não sei qual cadastro é {z}: o nome precisa conter CEIS, CNEP ou CEPIM")
                zips.append((cad, z))
        else:
            for cad in ("CEIS", "CNEP", "CEPIM"):
                dest, ok = tmp / f"{cad}.zip", False
                for back in range(12):  # tenta hoje e os dias anteriores
                    d = dt.date.today() - dt.timedelta(days=back)
                    url = BASE.format(k=cad.lower(), d=d)
                    print("tentando", url)
                    if download(url, dest):
                        ok = True
                        break
                if not ok:
                    sys.exit(f"Não consegui baixar o {cad}. Os dados antigos foram mantidos.")
                zips.append((cad, str(dest)))
        build_sancoes(zips)
    else:
        zips = list(a.zip)
        if not zips:
            urls = [u.strip() for u in pathlib.Path("scripts/pgfn_urls.txt").read_text().splitlines() if u.strip() and not u.startswith("#")]
            if not urls:
                print("scripts/pgfn_urls.txt está vazio: PGFN não será publicada (o site mostra 'fonte ainda não publicada').")
                return
            for i, u in enumerate(urls):
                dest = tmp / f"pgfn{i}.zip"
                print("baixando", u)
                if not download(u, dest):
                    sys.exit(f"Falha ao baixar {u}")
                zips.append(str(dest))
        build_pgfn(zips)


if __name__ == "__main__":
    main()
