"""Job keywords against resume.md without an LLM, like an employer's ATS keyword screen.

Crude by design: a fixed biotech/pharma R&D vocabulary plus acronyms from the job text,
after dropping benefits, EEO and company-blurb sentences. It is informational only and
never changes the LLM score, the score gate or any filter.
"""

import json
import re
import unicodedata

from app.resume import digest

KEYWORD_VERSION = "keywords-v2"

# Canonical name -> spellings. Spellings are normalised like the texts (lower case,
# hyphens and slashes as spaces, British spellings folded), so "scRNA-seq" also
# matches "scRNAseq" and "scrna seq"; a trailing plural is always accepted.
VOCABULARY: dict[str, list[str]] = {
    # Molecular and cell biology techniques
    "PCR": ["pcr"],
    "qPCR": ["qpcr", "rt qpcr", "real time pcr", "quantitative pcr", "ddpcr", "digital pcr"],
    "cloning": ["cloning", "molecular cloning", "gibson assembly"],
    "CRISPR": ["crispr", "cas9", "crispr cas9", "gene editing", "genome editing", "base editing"],
    "CRISPR screening": ["crispr screen", "pooled screen", "functional genomics screen"],
    "siRNA/shRNA": ["sirna", "shrna", "rnai", "rna interference"],
    "transfection": ["transfection", "electroporation", "nucleofection"],
    "lentiviral transduction": ["lentivir", "lentiviral transduction", "viral transduction"],
    "cell culture": [
        "cell culture",
        "tissue culture",
        "mammalian cell culture",
        "aseptic technique",
    ],
    "primary cells": ["primary cell", "pbmc", "primary human cell"],
    "iPSC": ["ipsc", "induced pluripotent stem cell", "stem cell"],
    "organoids": ["organoid", "3d cell culture", "spheroid"],
    "Western blot": ["western blot", "western blotting", "immunoblot", "immunoblotting"],
    "immunohistochemistry": ["immunohistochemistry", "ihc"],
    "immunofluorescence": ["immunofluorescence"],
    "microscopy": ["microscopy", "confocal", "live cell imaging", "fluorescence microscopy"],
    "high-content imaging": ["high content imaging", "high content screening", "operetta"],
    "histology": ["histology", "histopathology"],
    "flow cytometry": ["flow cytometry", "facs", "cell sorting"],
    "mass cytometry": ["mass cytometry", "cytof"],
    "ELISA": ["elisa"],
    "MSD": ["msd", "meso scale discovery"],
    "Luminex": ["luminex"],
    "AlphaLISA/HTRF": ["alphalisa", "alphascreen", "htrf", "tr fret"],
    "reporter assays": ["reporter assay", "luciferase assay", "reporter gene assay"],
    "cell-based assays": ["cell based assay", "cellular assay", "functional assay"],
    "biochemical assays": ["biochemical assay", "enzyme assay", "enzymatic assay"],
    "binding assays": ["binding assay"],
    "cytotoxicity assays": ["cytotoxicity assay", "killing assay", "viability assay"],
    "assay development": ["assay development", "assay design", "assay optimization"],
    "assay validation": ["assay validation", "method validation"],
    "high-throughput screening": ["high throughput screening", "hts", "compound screening"],
    "SPR": ["spr", "surface plasmon resonance", "biacore"],
    "BLI": ["bli", "biolayer interferometry", "octet"],
    "ITC": ["isothermal titration calorimetry"],
    "protein expression": ["protein expression", "recombinant protein", "protein production"],
    "protein purification": ["protein purification", "akta", "affinity chromatography"],
    "chromatography": ["chromatography", "fplc", "size exclusion", "sec", "ion exchange"],
    "HPLC": ["hplc", "uplc", "uhplc"],
    "mass spectrometry": ["mass spectrometry", "mass spec", "lc ms", "lc ms ms", "maldi"],
    "proteomics": ["proteomic"],
    "metabolomics": ["metabolomic", "lipidomic"],
    "NMR": ["nmr", "nuclear magnetic resonance"],
    "X-ray crystallography": ["crystallography", "x ray crystallography"],
    "cryo-EM": ["cryo em", "cryo electron microscopy", "cryoem"],
    "structural biology": ["structural biology"],
    "biophysics": ["biophysic", "biophysical characterization"],
    "protein engineering": ["protein engineering", "directed evolution"],
    "antibody engineering": ["antibody engineering", "antibody discovery", "phage display"],
    "hybridoma": ["hybridoma"],
    # Omics and sequencing
    "NGS": ["ngs", "next generation sequencing", "illumina", "nanopore", "pacbio"],
    "RNA-seq": ["rna seq", "rnaseq", "transcriptomic"],
    "single-cell RNA-seq": [
        "single cell rna seq",
        "single cell rna sequencing",
        "scrna seq",
        "single cell sequencing",
        "single cell transcriptomic",
        "10x genomics",
        "10x chromium",
    ],
    "spatial transcriptomics": ["spatial transcriptomic", "visium", "spatial omics", "merfish"],
    "ATAC-seq": ["atac seq"],
    "ChIP-seq": ["chip seq", "cut and run", "cut&run"],
    "whole-genome sequencing": ["whole genome sequencing", "wgs", "whole exome sequencing"],
    "genomics": ["genomic"],
    "epigenetics": ["epigenetic", "epigenomic", "dna methylation"],
    "multi-omics": ["multi omic", "multiomic"],
    "genetics": ["genetics", "human genetics", "population genetics"],
    "statistical genetics": [
        "statistical genetic",
        "gwas",
        "genome wide association",
        "fine mapping",
        "colocalization",
        "mendelian randomization",
        "polygenic risk score",
        "heritability",
    ],
    "QTL mapping": [
        "qtl",
        "eqtl",
        "pqtl",
        "sqtl",
        "quantitative trait loci",
        "quantitative trait locus",
    ],
    "molecular biology": ["molecular biology", "molecular biologist"],
    # Computational
    "bioinformatics": ["bioinformatic"],
    "computational biology": ["computational biology", "computational biologist"],
    "Python": ["python"],
    "R": ["r programming", "r bioconductor", "bioconductor", "tidyverse", "rstudio", "r shiny"],
    "SQL": ["sql"],
    "Linux/HPC": ["linux", "unix", "hpc", "high performance computing", "slurm"],
    "Nextflow/Snakemake": ["nextflow", "snakemake", "workflow manager", "wdl", "cromwell"],
    "cloud computing": ["aws", "gcp", "google cloud", "azure", "cloud computing"],
    "machine learning": ["machine learning", "deep learning", "ml", "ai", "neural network"],
    "statistics": ["statistic", "biostatistic", "statistical analysis", "regression"],
    "Bayesian methods": ["bayesian"],
    "causal inference": ["causal inference"],
    "Git": ["git", "github", "gitlab", "version control"],
    "containers": ["docker", "containerization", "singularity", "kubernetes"],
    "data analysis": ["data analysis", "data analytic"],
    "cheminformatics": ["cheminformatic", "rdkit"],
    "molecular modeling": ["molecular modeling", "molecular dynamics", "docking", "cadd"],
    "GraphPad Prism": ["graphpad", "prism"],
    "FlowJo": ["flowjo"],
    "ELN/LIMS": ["eln", "electronic lab notebook", "lims", "benchling"],
    "laboratory automation": [
        "lab automation",
        "laboratory automation",
        "liquid handling",
        "hamilton",
        "tecan",
        "opentrons",
        "echo acoustic",
    ],
    # Modalities
    "small molecules": ["small molecule"],
    "biologics": ["biologic", "large molecule"],
    "monoclonal antibodies": ["monoclonal antibody", "mab", "therapeutic antibody"],
    "antibodies": ["antibody", "antibodies"],
    "bispecifics": ["bispecific", "multispecific"],
    "ADCs": ["adc", "antibody drug conjugate"],
    "cell therapy": ["cell therapy", "cell therapies"],
    "CAR-T": ["car t", "car nk", "chimeric antigen receptor"],
    "gene therapy": ["gene therapy", "gene therapies"],
    "AAV": ["aav", "adeno associated virus", "viral vector"],
    "mRNA": ["mrna"],
    "lipid nanoparticles": ["lnp", "lipid nanoparticle"],
    "oligonucleotides": ["oligonucleotide", "antisense", "aso"],
    "peptides": ["peptide"],
    "vaccines": ["vaccine"],
    "PROTACs/degraders": ["protac", "targeted protein degradation", "molecular glue", "degrader"],
    "radiopharmaceuticals": ["radiopharmaceutical", "radioligand"],
    "microbiome": ["microbiome"],
    # Therapeutic areas and biology
    "oncology": ["oncology", "cancer", "tumor biology", "solid tumor", "hematological malignanc"],
    "immuno-oncology": ["immuno oncology", "io", "checkpoint inhibitor"],
    "immunology": [
        "immunology",
        "immunologist",
        "immune cell",
        "t cell",
        "b cell",
        "nk cell",
        "macrophage",
    ],
    "inflammation": ["inflammation", "inflammatory", "autoimmun"],
    "neuroscience": [
        "neuroscience",
        "neurodegeneration",
        "neurodegenerative",
        "neurology",
        "neuron",
    ],
    "cardiovascular": ["cardiovascular", "cardiometabolic"],
    "metabolic disease": ["metabolic disease", "obesity", "diabetes"],
    "infectious disease": [
        "infectious disease",
        "virology",
        "bacteriology",
        "antimicrobial",
        "antiviral",
    ],
    "rare disease": ["rare disease", "orphan disease"],
    "fibrosis": ["fibrosis", "fibrotic"],
    "respiratory": ["respiratory"],
    "dermatology": ["dermatology"],
    "ophthalmology": ["ophthalmology", "retina"],
    "hematology": ["hematology"],
    "nephrology": ["nephrology", "kidney disease", "renal"],
    "translational research": ["translational"],
    "biomarkers": ["biomarker"],
    "target identification": ["target identification", "target id", "target discovery"],
    "target validation": ["target validation"],
    "drug discovery": ["drug discovery"],
    "hit-to-lead": ["hit to lead", "hit identification", "lead optimization"],
    "medicinal chemistry": ["medicinal chemistry", "med chem"],
    "synthetic chemistry": ["synthetic chemistry", "organic synthesis", "organic chemistry"],
    "SAR": ["sar", "structure activity relationship"],
    "pharmacology": ["pharmacology", "pharmacologist"],
    "in vivo": ["in vivo", "animal model", "mouse model", "rodent model", "xenograft"],
    "in vitro": ["in vitro"],
    "ex vivo": ["ex vivo"],
    "PK/PD": ["pk pd", "pharmacokinetic", "pharmacodynamic", "pkpd"],
    "DMPK/ADME": ["dmpk", "adme"],
    "toxicology": ["toxicology", "safety assessment"],
    "bioanalysis": ["bioanalysis", "bioanalytical"],
    "immunogenicity": ["immunogenicity"],
    "preclinical": ["preclinical", "pre clinical", "nonclinical", "non clinical"],
    "IND-enabling studies": ["ind enabling", "ind", "cta", "ind filing"],
    "clinical trials": ["clinical trial", "clinical development", "clinical stage"],
    # Process, quality and regulatory
    "process development": ["process development"],
    "upstream processing": [
        "upstream process",
        "upstream development",
        "bioreactor",
        "fermentation",
        "cell line development",
    ],
    "downstream processing": [
        "downstream process",
        "downstream development",
        "tangential flow filtration",
    ],
    "analytical development": [
        "analytical development",
        "analytical method development",
        "analytical characterization",
    ],
    "formulation": ["formulation"],
    "CMC": ["cmc", "chemistry manufacturing and controls"],
    "tech transfer": ["tech transfer", "technology transfer"],
    "scale-up": ["scale up"],
    "GMP": ["gmp", "cgmp", "good manufacturing practice"],
    "GLP": ["glp", "good laboratory practice"],
    "GCP": ["good clinical practice"],
    "quality control": ["quality control", "qc"],
    "quality assurance": ["quality assurance"],
    "regulatory submissions": [
        "regulatory submission",
        "regulatory filing",
        "regulatory affairs",
        "regulatory document",
    ],
    "FDA/EMA/MHRA": ["fda", "ema", "mhra", "ich guideline", "ich"],
    "design of experiments": ["design of experiments", "doe"],
    "SOPs": ["sop", "standard operating procedure"],
    "biosafety": ["biosafety", "bsl2", "bsl 2", "category 2 containment"],
    "CRO management": ["cro", "contract research organization", "external partner"],
    # Degree and role signals that ATS screens commonly carry
    "PhD": ["phd", "ph d", "doctorate", "doctoral degree"],
    "postdoctoral": ["postdoc", "post doc", "postdoctoral"],
    "line management": ["line management", "line manage", "people management", "direct reports"],
    "project management": ["project management", "project manager", "project lead"],
    "cross-functional": ["cross functional", "multidisciplinary", "matrix team"],
    "scientific publications": ["publication", "peer reviewed", "first author"],
}

# Short spellings that are ordinary words in some contexts: matched in the original case only
# ("SEC", "IND", "IO", "AI", "ML", "QC" ...), never as lower-case prose ("io", "ind", "sec").
CASE_SENSITIVE = {
    "sec": "SEC",
    "ind": "IND",
    "io": "IO",
    "ai": "AI",
    "ml": "ML",
    "qc": "QC",
    "ema": "EMA",
    "ich": "ICH",
    "cta": "CTA",
    "cro": "CRO",
    "aso": "ASO",
    "adc": "ADC",
    "sar": "SAR",
    "doe": "DoE",
    "eln": "ELN",
    "msd": "MSD",
    "spr": "SPR",
    "bli": "BLI",
    "sop": "SOP",
    "lnp": "LNP",
    "prism": "Prism",
}

# Acronyms that carry no skill: places, legal forms, HR and document words.
STOP_ACRONYMS = {
    *[
        "AND",
        "OR",
        "THE",
        "TO",
        "OF",
        "IN",
        "FOR",
        "WE",
        "YOU",
        "OUR",
        "ON",
        "AT",
        "IS",
        "AS",
        "BE",
        "BY",
        "ARE",
        "ALL",
        "NEW",
        "CAN",
        "WILL",
        "WITH",
    ],
    *[
        "UK",
        "US",
        "USA",
        "EU",
        "EEA",
        "GB",
        "NHS",
        "EEO",
        "EOE",
        "HR",
        "CV",
        "CEO",
        "CFO",
        "CSO",
        "CTO",
        "COO",
        "VP",
        "SVP",
        "EVP",
        "OK",
        "ID",
    ],
    *[
        "LTD",
        "LLC",
        "INC",
        "PLC",
        "GMBH",
        "AG",
        "SA",
        "BV",
        "NV",
        "CO",
        "HQ",
        "FAQ",
        "PDF",
        "URL",
        "WWW",
        "HTTP",
        "HTTPS",
        "FTE",
        "PTO",
    ],
    *[
        "BSC",
        "MSC",
        "MS",
        "BA",
        "BS",
        "MA",
        "MBA",
        "MD",
        "DPHIL",
        "MPHIL",
        "NA",
        "TBC",
        "TBD",
        "ASAP",
        "ETC",
        "EG",
        "IE",
        "JR",
        "SR",
        "II",
        "III",
        "IV",
    ],
    *[
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "AM",
        "PM",
        "UTC",
        "GMT",
        "BST",
        "EST",
        "PST",
        "KPI",
        "KPIS",
        "OKR",
        "LGBTQ",
        "LGBT",
        "LGBTQIA",
    ],
    *[
        "YOUR",
        "ROLE",
        "ABOUT",
        "WHAT",
        "WHO",
        "WHY",
        "HOW",
        "KEY",
        "JOB",
        "TEAM",
        "WORK",
        "NOT",
        "ONLY",
        "MUST",
        "HAVE",
        "SKILLS",
    ],
}

_EMPLOYER_SUFFIX = re.compile(
    r"(?:[\s,]+(?:ltd|limited|llc|inc|plc|gmbh|ag|sa|bv|nv|co|corp|corporation|group|holdings"
    r"|labs?|laboratories|therapeutics|bio|biosciences?|biotech(?:nology)?|pharma(?:ceuticals?)?"
    r"|technologies|sciences?|uk|us|usa)\.?)+$",
    re.IGNORECASE,
)
_WORK_MODES = {"remote", "hybrid", "on site", "onsite", "on-site", "office", "flexible"}

# Headings in collapsed job text: boilerplate starts, job content resumes.
_BOILERPLATE_HEADING = re.compile(
    r"\b(?:what we offer|what we can offer|what's in it for you|why join(?: us)?|why work (?:for|with) us"
    r"|our offer|(?:our |the )?benefits(?: package)?\s*(?::|include|we offer)|perks\s*(?::|include)"
    r"|equal (?:employment )?opportunit|eeo statement|diversity,? (?:equity,? )?(?:and|&) inclusion"
    r"|privacy (?:notice|policy)|to (?:all )?recruitment agencies|recruiters? and agencies"
    r"|your (?:package|rewards?)|compensation (?:and|&) (?:benefits|growth)|ready to (?:build|join|apply)"
    r"|life at \w+|our (?:culture|values)\s*:)",
    re.IGNORECASE,
)
_CONTENT_HEADING = re.compile(
    r"\b(?:about the role|the role\s*:|the opportunity|role overview|position summary"
    r"|(?:key )?responsibilities|what you(?:'ll| will) (?:do|be doing|bring|need)"
    r"|(?:essential |key |minimum |preferred |desirable )?(?:requirements|qualifications)"
    r"|about you|who you are|you(?:'ll| will) have|skills (?:and|&) experience"
    r"|experience (?:and|&) skills|essential\s*:|desirable\s*:|nice to have|preferred\s*:"
    r"|in this role|you will be (?:responsible|expected)|the successful candidate|role purpose|purpose of the role"
    r"|your (?:impact|role|responsibilities)|a day in the life|(?:it'?s|it’s) a bonus if"
    r"|you(?:'ll|’ll| will) bring|what (?:we'?re|we’re|we are) looking for)",
    re.IGNORECASE,
)
# Company blurb headings: skipped only up to a later content heading, since a blurb often
# opens the text and runs straight into the job with no heading of its own.
_BLURB_HEADING = re.compile(
    r"\b(?:about us\b|about the company|about the organi[sz]ation|who we are|our company|our mission"
    r"|the mission\b|why we exist|our story|our (?:culture|values)\b"
    r"|about the (?:[A-Z][\w&-]* ){1,4}(?:team|group|function|department)\b)",
    re.IGNORECASE,
)
# Single boilerplate sentences that can sit anywhere in the text.
_BOILERPLATE_SENTENCE = re.compile(
    r"\b(?:equal opportunit|without regard to|regardless of (?:age|race|gender|sex|religion)"
    r"|protected (?:characteristic|veteran|status)|reasonable (?:adjustment|accommodation)"
    r"|sexual orientation|gender identity|marital|pregnan|veteran status|disabilit"
    r"|pension|private medical|health insurance|dental|life (?:assurance|insurance)"
    r"|annual leave|holiday|paid time off|parental leave|maternity|paternity|cycle to work"
    r"|share options|stock options|equity (?:package|grant)|bonus|salary|compensation"
    r"|wellbeing|well being|gym|flexible working|hybrid working|free (?:lunch|breakfast|snacks)"
    r"|privacy|personal data|gdpr|recruitment agenc|unsolicited|right to work|visa sponsorship"
    r"|founded in|headquartered|backed by|series [a-e]\b|raised \$|venture capital"
    r"|award[- ]winning|great place to work|join (?:us|our team) (?:in|on|as)"
    r"|^\s*(?:job )?locations?\s*:|apply now)",
    re.IGNORECASE,
)

_FOLDS = [
    (r"tumour", "tumor"),
    (r"haem", "hem"),
    (r"leukaem", "leukem"),
    (r"anaem", "anem"),
    (r"foet", "fet"),
    (r"paediatr", "pediatr"),
    (r"programme", "program"),
    (r"modelling", "modeling"),
    (r"labelling", "labeling"),
    (r"(?<=[a-z]{3})is(?=(?:e|ed|es|ing|ation|ations)\b)", "iz"),
    (r"(?<=[a-z])yse(?=[ds]?\b)", "yze"),
]


def normalize(value: str) -> str:
    """Lower case, hyphens/slashes/underscores as spaces, British spellings folded."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"[‐-―\-/_|]+", " ", value)
    value = value.replace("’", "'").replace("'", "")
    value = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", value)  # "Ph.D." -> "phd."
    for pattern, repl in _FOLDS:
        value = re.sub(pattern, repl, value)
    return re.sub(r"\s+", " ", value).strip()


def _spelling_pattern(spelling: str) -> str:
    words = [re.escape(w) for w in normalize(spelling).split()]
    last = words[-1]
    if last.endswith("y") and len(last) > 3 and last[-2] not in "aeiou":
        words[-1] = last[:-1] + "(?:y|ies)"
    else:
        words[-1] = last + "(?:e?s)?"
    # Spaces stand for hyphens and joins too: "RNA-seq", "RNA seq" and "RNAseq".
    return r"(?<![a-z0-9])" + r"\s?".join(words) + r"(?![a-z0-9])"


def _compile():
    compiled = {}
    for name, spellings in VOCABULARY.items():
        loose = [s for s in spellings if normalize(s) not in CASE_SENSITIVE]
        strict = [CASE_SENSITIVE[normalize(s)] for s in spellings if normalize(s) in CASE_SENSITIVE]
        compiled[name] = (
            re.compile("|".join(_spelling_pattern(s) for s in loose)) if loose else None,
            re.compile(
                "|".join(rf"(?<![A-Za-z0-9]){re.escape(s)}s?(?![A-Za-z0-9])" for s in strict)
            )
            if strict
            else None,
        )
    return compiled


_TERMS = _compile()
_ACRONYM = re.compile(
    r"(?<![A-Za-z0-9&])([A-Z][A-Za-z0-9]*[A-Z0-9](?:-[A-Z0-9]+)?)s?(?![A-Za-z0-9&])"
)


def _part_of_term(text: str, match: re.Match) -> bool:
    """The acronym sits inside a vocabulary match nearby ("RNA" in "single-cell RNA-seq")."""
    before = text[: match.start()].split()[-3:]
    after = text[match.end() :].split()[:3]
    # The acronym's own whitespace word may continue past it ("LC-MS/MS", "RNA-seq").
    head = before.pop() if before and not text[: match.start()][-1:].isspace() else ""
    window = " ".join([*before, head + text[match.start() : match.end()], *after])
    folded, word = normalize(window), normalize(match.group(1))
    for name in VOCABULARY:
        for start, end, kind in _find(name, window, folded):
            if word in (folded if kind == "n" else window.casefold())[start:end]:
                return True
    return False


def _variants(employer: str) -> list[str]:
    """The employer's name and its obvious variants: without legal forms or a sector word."""
    name = re.sub(r"\s+", " ", employer or "").strip(" ,.")
    if not name:
        return []
    out = {name}
    short = re.sub(_EMPLOYER_SUFFIX, "", name).strip(" ,.")
    if short and len(short) > 2:
        out.add(short)
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) > 1:
        out.add("".join(w[0] for w in words).upper())  # "Isomorphic Labs" -> "IL"
    return sorted(out, key=len, reverse=True)


def _place_names(location: str) -> list[str]:
    """Locations of the job and their abbreviations ("Research Triangle Park" -> "RTP")."""
    out = set()
    for part in re.split(r"[;,/|()]|\s+or\s+|\s+and\s+", location or ""):
        part = part.strip(" .-")
        if len(part) < 2 or part.casefold() in _WORK_MODES:
            continue
        out.add(part)
        words = re.findall(r"[A-Za-z]+", part)
        if len(words) > 1:
            out.add("".join(w[0] for w in words).upper())
    return sorted(out, key=len, reverse=True)


def _without(text: str, names: list[str]) -> str:
    for name in names:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(name)}(?:[’']s)?(?![A-Za-z0-9])", " ", text)
    return text


def job_text(
    title: str,
    description: str,
    requirements: str | None = None,
    *,
    employer: str = "",
    location: str = "",
) -> str:
    """Job content without benefits, EEO, privacy and company-blurb text.

    The employer's name and the job's places are removed from the description: they describe
    the company and the office, not a skill ("Genomics" the employer, "RTP" the site).
    """
    raw = " ".join(x for x in (description, requirements or "") if x)
    names = _variants(employer)
    content = [m.start() for m in _CONTENT_HEADING.finditer(raw)]
    marks = sorted(
        [(m.start(), False) for m in _BOILERPLATE_HEADING.finditer(raw)]
        + [(x, True) for x in content]
        + [
            (m.start(), False)
            for m in _BLURB_HEADING.finditer(raw)
            if any(x > m.start() for x in content)
        ]
    )
    # Text before the first content heading that names the employer is the company blurb.
    if (
        content
        and content[0] > 0
        and names
        and _without(raw[: content[0]], names) != raw[: content[0]]
    ):
        marks.append((0, False))
        marks.sort()
    kept, start, keep = [], 0, True
    for position, content_mark in marks:
        if keep:
            kept.append(raw[start:position])
        start, keep = position, content_mark
    if keep:
        kept.append(raw[start:])
    # Each heading starts a sentence. Bullets collapsed into " - " or "• " separators are sentences of their own, so one
    # benefits line cannot drop a whole requirements list.
    sentences = re.split(
        r"(?<=[.!?;•·])\s+|\s+[•·▪◦]\s*|\s+[-–—]\s+(?=[A-Z(])|\n+", "\n".join(kept)
    )
    body = " ".join(s for s in sentences if s.strip() and not _BOILERPLATE_SENTENCE.search(s))
    body = _without(body, names + _place_names(location))
    return " ".join(x for x in (title, re.sub(r"\s+", " ", body).strip()) if x)


def _find(name: str, text: str, folded: str) -> list[tuple[int, int, str]]:
    loose, strict = _TERMS[name]
    spans = [(m.start(), m.end(), "n") for m in loose.finditer(folded)] if loose else []
    if strict:
        spans += [(m.start(), m.end(), "o") for m in strict.finditer(text)]
    return spans


def _in_resume(name: str, resume: str, resume_folded: str) -> bool:
    return bool(_find(name, resume, resume_folded))


def keyword_match(
    title: str,
    description: str,
    resume: str,
    *,
    requirements: str | None = None,
    employer: str = "",
    location: str = "",
) -> dict | None:
    """{score, present, missing, total} over job keywords, or None when none were found.

    present/missing are ordered by how often the job mentions them, then first mention.
    """
    text = job_text(title, description, requirements, employer=employer, location=location)
    folded = normalize(text)
    found = {}
    for name in VOCABULARY:
        spans = _find(name, text, folded)
        if spans:
            found[name] = spans
    # A term only seen inside a longer term ("RNA-seq" in "single-cell RNA-seq") is not separate.
    terms = {}
    for name, spans in found.items():
        alone = [
            s
            for s in spans
            if not any(
                other != name
                and o[2] == s[2]
                and o[0] <= s[0]
                and s[1] <= o[1]
                and (o[1] - o[0]) > (s[1] - s[0])
                for other, others in found.items()
                for o in others
            )
        ]
        if alone:
            terms[name] = (len(alone), min(s[0] for s in alone))
    employer_words = {w.casefold() for w in re.findall(r"[A-Za-z0-9]+", employer or "")}
    employer_words |= {n.casefold() for n in _variants(employer) + _place_names(location)}
    for match in _ACRONYM.finditer(text):
        word = match.group(1)
        letters = sum(c.isalpha() for c in word)
        if (
            word.upper() in STOP_ACRONYMS
            or (word.isalpha() and word.isupper() and len(word) > 6)
            or word.casefold() in employer_words
            or letters < 2
            or sum(c.isdigit() for c in word) > 3
            or sum(c.isupper() for c in word) < 2
            or _part_of_term(text, match)
        ):
            continue
        count, first = terms.get(word, (0, match.start()))
        terms[word] = (count + 1, first)
    if not terms:
        return None
    resume_folded = normalize(resume)
    order = sorted(terms, key=lambda k: (-terms[k][0], terms[k][1], k))
    present, missing = [], []
    for name in order:
        if name in VOCABULARY:
            has = _in_resume(name, resume, resume_folded)
        else:
            has = bool(re.search(_spelling_pattern(name), resume_folded))
        (present if has else missing).append(name)
    return {
        "score": round(100 * len(present) / len(order)),
        "present": present,
        "missing": missing,
        "total": len(order),
    }


def match_hash(row: dict, resume: str) -> str:
    return digest(
        {
            "version": KEYWORD_VERSION,
            "job": [
                row.get(k) for k in ("title", "employer", "location", "description", "requirements")
            ],
            "resume": resume,
        }
    )


def attach_keyword_matches(conn, cfg, rows):
    """Add row["keywords"] from the cache; recompute when the job or resume.md changed."""
    try:
        resume = cfg.resolved(cfg.resume_path).read_text(encoding="utf-8").strip()
    except OSError:
        resume = ""
    cached = {
        r["job_id"]: (r["content_hash"], r["payload"])
        for r in conn.execute("SELECT job_id,content_hash,payload FROM keyword_matches")
    }
    changed = False
    for row in rows:
        row["keywords_resume"] = bool(resume)
        if not resume:
            row["keywords"] = None
            continue
        key = match_hash(row, resume)
        hit = cached.get(row["id"])
        if hit and hit[0] == key:
            row["keywords"] = json.loads(hit[1])
            continue
        row["keywords"] = keyword_match(
            row.get("title") or "",
            row.get("description") or "",
            resume,
            requirements=row.get("requirements"),
            employer=row.get("employer") or "",
            location=row.get("location") or "",
        )
        conn.execute(
            """INSERT INTO keyword_matches(job_id,content_hash,payload) VALUES (?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET content_hash=excluded.content_hash,
            payload=excluded.payload,
            computed_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')""",
            (row["id"], key, json.dumps(row["keywords"])),
        )
        changed = True
    if changed:
        conn.commit()
    return rows


def summary(match: dict | None, limit: int = 3) -> str:
    """One short cell: percentage, counts and the first missing keywords."""
    if not match:
        return "no keywords found"
    text = f"{match['score']}% ({len(match['present'])}/{match['total']})"
    if match["missing"]:
        text += "\nmissing: " + ", ".join(match["missing"][:limit])
    return text
