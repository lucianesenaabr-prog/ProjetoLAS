#!/usr/bin/env python3
"""TeCo: baixa listas públicas e gera índices por CNPJ (pedaços de JSON) para o site.

Uso:
  python scripts/build_dados.py sancoes                 # baixa CEIS, CNEP e CEPIM (Portal da Transparência)
  python scripts/build_dados.py sancoes --zip ceis.zip --zip cnep.zip --zip cepim.zip   # usa arquivos locais
  python scripts/build_dados.py pgfn                    # baixa os .zip listados em scripts/pgfn_urls.txt
  python scripts/build_dados.py fgts                    # baixa os .zip listados em scripts/fgts_urls.txt
  python scripts/build_dados.py pgfn --zip arquivo.zip  # usa arquivos locais (vale também para fgts)
  python scripts/build_dados.py listasuja               # baixa o PDF do Cadastro de Empregadores (MTE)
  python scripts/build_dados.py listasuja --pdf arquivo.pdf   # usa um PDF local

Saída: dados/<fonte>/<3 primeiros dígitos>.json e dados/<fonte>/meta.json
       (fontes: sancoes, pgfn, fgts, listasuja).
"""
import argparse, csv, datetime as dt, io, json, pathlib, re, subprocess, sys, tempfile, unicodedata, urllib.error, urllib.request, zipfile

csv.field_size_limit(1 << 24)
OUT = pathlib.Path("dados")
UA = {"User-Agent": "Mozilla/5.0 (compatible; TeCo/1.0)"}
BASE = "https://portaldatransparencia.gov.br/download-de-dados/{k}/{d:%Y%m%d}"
REG = re.compile(r"NEGOCI|PARCEL|GARANT|SUSPEN|DISCUT")  # heurística: situações "regularizadas" ou em discussão
DIAS_ATRAS = 60  # o portal não publica toda base todo dia (o CEPIM às vezes fica semanas sem arquivo novo)
LISTA_URL = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.pdf"
LISTA_LOCAL = pathlib.Path("scripts/cadastro_empregadores.pdf")  # plano B: PDF baixado à mão e enviado ao repositório
DIVIDAS = {
    "pgfn": ("scripts/pgfn_urls.txt", "PGFN: dados abertos da dívida ativa da União"),
    "fgts": ("scripts/fgts_urls.txt", "PGFN: dados abertos da dívida de FGTS"),
}
# CNPJ ou CPF. Tolera quebra de linha ou hífen perdido no fim da coluna (ex.: "0001-" na linha de cima e "01" na de baixo).
RE_DOC = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}[-\s]{0,2}\d{2}|\d{3}\.\d{3}\.\d{3}[-\s]{0,2}\d{2}")
RE_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def digits(s):
    return re.sub(r"\D", "", s or "")


def download(url, dest, magic=b"PK"):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        with open(dest, "rb") as f:
            return f.read(len(magic)) == magic
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  falhou: {url} ({e})")
        return False


def csv_rows(zpath):
    """Gera (função de índice de coluna, linha, cabeçalho) para cada linha de cada .csv do zip."""
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as fh:
                rd = csv.reader(io.TextIOWrapper(fh, encoding="latin-1", newline=""), delimiter=";")
                hdr = [norm(h) for h in next(rd)]
                print(f"  {name}: colunas = {hdr}")
                ix = lambda *k, hdr=hdr: next((i for i, h in enumerate(hdr) if all(x in h for x in k)), None)
                for row in rd:
                    yield ix, row, hdr


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


def build_sancoes(zips, datas_arq=None):
    today, data, datas = dt.date.today(), {}, []
    for cad, zpath in zips:
        n = 0
        for ix, row, hdr in csv_rows(zpath):
            if ix("CNPJ") is None:
                sys.exit(f"{cad}: não achei a coluna do CNPJ. Cabeçalho lido: {hdr}")
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
    write_shards(data, OUT / "sancoes", {"data": today.isoformat(), "datas": datas_arq or {}, "fonte": "Portal da Transparência (CGU): CEIS, CNEP, CEPIM"})


def build_divida(zips, modo):
    """Dívida ativa (pgfn) ou dívida de FGTS (fgts): soma o devedor principal por CNPJ."""
    agg, last = {}, None
    for zpath in zips:
        for ix, row, hdr in csv_rows(zpath):
            if hdr is not last:  # novo arquivo: acha as colunas uma vez só
                last = hdr
                c_cnpj, c_val, c_dev = ix("CNPJ"), ix("VALOR", "CONSOLIDADO"), ix("TIPO", "DEVEDOR")
                # a PGFN tem mais de uma coluna de situação (tipo e situação da inscrição): olha todas
                c_sit = [i for i, h in enumerate(hdr) if "SITUACAO" in h and "INSCRICAO" in h]
                if c_cnpj is None or c_val is None:
                    sys.exit(f"{modo}: não achei a coluna do CNPJ ou do valor consolidado. Cabeçalho lido: {hdr}")
                if not c_sit:
                    print(f"  AVISO: sem coluna de situação da inscrição em {hdr}; toda a dívida será tratada como em cobrança")
            cn = digits(cell(row, c_cnpj))
            if len(cn) != 14:
                continue
            if c_dev is not None and "PRINCIPAL" not in norm(cell(row, c_dev)):
                continue  # só devedor principal, como orienta a PGFN
            v = cell(row, c_val).replace(".", "").replace(",", ".")
            try:
                v = float(v)
            except ValueError:
                continue
            a = agg.setdefault(cn, [0.0, 0.0, 0])
            a[0] += v
            a[2] += 1
            if not REG.search(" ".join(norm(cell(row, i)) for i in c_sit)):
                a[1] += v
    data = {k: {"t": round(a[0], 2), "i": round(a[1], 2), "n": a[2]} for k, a in agg.items()}
    write_shards(data, OUT / modo, {"data": dt.date.today().isoformat(), "fonte": DIVIDAS[modo][1]})


def pdf_texto(path):
    """Texto do PDF: usa o pypdf; se não estiver instalado, tenta o pdftotext."""
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    except ImportError:
        return subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout


def parse_lista(texto):
    """Lê o texto do Cadastro de Empregadores. Devolve ({cnpj: [registros]}, data de atualização, nº de CPFs ignorados).

    Cada linha da tabela tem: ID, ano, UF, empregador, CNPJ/CPF, estabelecimento, trabalhadores, CNAE,
    decisão administrativa (data) e inclusão no cadastro (data). A inclusão é a última data antes do próximo CNPJ/CPF.
    """
    t = re.sub(r"\s+", " ", texto)
    m = re.search(r"atualizado em (\d{2}/\d{2}/\d{4})", t, re.I)
    ref = m.group(1) if m else ""
    t = re.sub(r"atualizado em \d{2}/\d{2}/\d{4}", " ", t, flags=re.I)  # cabeçalho repetido não pode virar data de linha
    ms = list(RE_DOC.finditer(t))
    data, cpfs = {}, 0
    for k, m in enumerate(ms):
        cn = digits(m.group())
        if len(cn) != 14:
            cpfs += 1  # CPF (pessoa física): só serve para separar as linhas
            continue
        fim = ms[k + 1].start() if k + 1 < len(ms) else len(t)
        datas = RE_DATA.findall(t[m.end():fim])
        data.setdefault(cn, []).append({"c": "MTE", "i": datas[-1] if datas else ""})
    return data, ref, cpfs


def build_listasuja(pdf):
    data, ref, cpfs = parse_lista(pdf_texto(pdf))
    n = sum(len(v) for v in data.values())
    print(f"  lista suja: {n} linhas com CNPJ, {cpfs} com CPF (ignoradas); atualizada em {ref or '?'}")
    if n < 20:
        sys.exit("Li menos de 20 empregadores com CNPJ: o formato do PDF pode ter mudado. A lista suja não será publicada.")
    write_shards(data, OUT / "listasuja", {"data": dt.date.today().isoformat(), "ref": ref, "fonte": "Ministério do Trabalho e Emprego: Cadastro de Empregadores"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["sancoes", "pgfn", "fgts", "listasuja"])
    ap.add_argument("--zip", action="append", default=[])
    ap.add_argument("--pdf")
    a = ap.parse_args()
    tmp = pathlib.Path(tempfile.mkdtemp())
    if a.modo == "sancoes":
        zips, datas = [], {}
        if a.zip:
            for z in a.zip:
                cad = next((c for c in ("CEIS", "CNEP", "CEPIM") if c in z.upper()), None)
                if not cad:
                    sys.exit(f"Não sei qual cadastro é {z}: o nome precisa conter CEIS, CNEP ou CEPIM")
                zips.append((cad, z))
        else:
            for cad in ("CEIS", "CNEP", "CEPIM"):
                dest, ok = tmp / f"{cad}.zip", False
                for back in range(DIAS_ATRAS):  # tenta hoje e os dias anteriores
                    d = dt.date.today() - dt.timedelta(days=back)
                    url = BASE.format(k=cad.lower(), d=d)
                    print("tentando", url)
                    if download(url, dest):
                        ok, datas[cad] = True, d.isoformat()
                        print(f"  {cad}: arquivo de {d:%d/%m/%Y}")
                        break
                if not ok:
                    sys.exit(f"Não consegui baixar o {cad} nos últimos {DIAS_ATRAS} dias. Os dados antigos foram mantidos.")
                zips.append((cad, str(dest)))
        build_sancoes(zips, datas)
    elif a.modo == "listasuja":
        pdf = a.pdf
        if not pdf:
            dest = tmp / "lista.pdf"
            print("baixando", LISTA_URL)
            if download(LISTA_URL, dest, b"%PDF"):
                pdf = str(dest)
            elif LISTA_LOCAL.exists():
                print(f"usando o PDF enviado ao repositório: {LISTA_LOCAL}")
                pdf = str(LISTA_LOCAL)
            else:
                sys.exit(f"Não consegui baixar a lista suja. Baixe o PDF no site do Ministério do Trabalho e envie como {LISTA_LOCAL}.")
        build_listasuja(pdf)
    else:
        zips = list(a.zip)
        arq, _ = DIVIDAS[a.modo]
        if not zips:
            urls = [u.strip() for u in pathlib.Path(arq).read_text().splitlines() if u.strip() and not u.startswith("#")]
            if not urls:
                print(f"{arq} está vazio: {a.modo.upper()} não será publicada (o site mostra 'fonte ainda não publicada').")
                return
            for i, u in enumerate(urls):
                dest = tmp / f"{a.modo}{i}.zip"
                print("baixando", u)
                if not download(u, dest):
                    sys.exit(f"Falha ao baixar {u}")
                zips.append(str(dest))
        build_divida(zips, a.modo)


if __name__ == "__main__":
    main()
