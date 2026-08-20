#!/bin/bash
set -euo pipefail

# ============================================================
#  run_campaign.sh — barcha topologiya x routing rejim x seed
#  kombinatsiyalarini ketma-ket ishga tushirib, har run'ning
#  butun datasets/ papkasini (metadata.json bilan birga)
#  results/<topo>_<mode>_seed<s>/datasets/ ga ko'chiradi.
#
#  Bu layout aynan results/combine_datasets.py kutgan layout:
#      results/<topo>_<mode>[_seed<N>]/datasets/*.csv
#  Shuning uchun kampaniyadan so'ng to'g'ridan-to'g'ri:
#      python3 results/combine_datasets.py
#  ishlaydi.
#
#  Mininet root talab qiladi -> sudo bilan ishga tushiring.
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Sozlamalar (env yoki flag orqali override qilinadi) ───
TOPOLOGIES=(three_as five_as datacenter campus)
MODES=(l2_learn rip ospf isis eigrp bgp ecmp spf policy static hybrid)
SEEDS=(0)
DURATION=300
DATA_DIR="${DATA_DIR:-/data}"     # light_simulation.py datasetlarni shu yerga yozadi
RESULTS_DIR="$ROOT_DIR/results"
DRY_RUN=0

usage() {
    cat <<EOF
Foydalanish: sudo bash run_campaign.sh [flaglar]

  --topologies "t1 t2 ..."   Topologiyalar (default: ${TOPOLOGIES[*]})
  --modes "m1 m2 ..."        Routing rejimlar (default: 11 ta rejim)
  --seeds "0 1 2"            Har kombinatsiya uchun seed'lar (default: ${SEEDS[*]})
  --duration N               Har run davomiyligi soniyada (default: $DURATION)
  --data-dir PATH            light_simulation.py chiqish papkasi (default: $DATA_DIR)
  --results-dir PATH         Natijalar ko'chiriladigan papka (default: <repo>/results)
  --dry-run                  Faqat rejalashtirilgan buyruqlarni chop etish, ishga tushirmaslik
  -h, --help                 Shu yordam

Misol:
  sudo bash run_campaign.sh --topologies "five_as" --seeds "0 1 2" --duration 300
EOF
}

# ─── Flag parsing ───
while [[ $# -gt 0 ]]; do
    case "$1" in
        --topologies)  read -r -a TOPOLOGIES <<< "$2"; shift 2 ;;
        --modes)       read -r -a MODES <<< "$2"; shift 2 ;;
        --seeds)       read -r -a SEEDS <<< "$2"; shift 2 ;;
        --duration)    DURATION="$2"; shift 2 ;;
        --data-dir)    DATA_DIR="$2"; shift 2 ;;
        --results-dir) RESULTS_DIR="$2"; shift 2 ;;
        --dry-run)     DRY_RUN=1; shift ;;
        -h|--help)     usage; exit 0 ;;
        *) echo "Noma'lum flag: $1" >&2; usage; exit 1 ;;
    esac
done

total=$(( ${#TOPOLOGIES[@]} * ${#MODES[@]} * ${#SEEDS[@]} ))
echo "Kampaniya: ${#TOPOLOGIES[@]} topologiya x ${#MODES[@]} rejim x ${#SEEDS[@]} seed = $total run"
echo "Chiqish:   $RESULTS_DIR/<topo>_<mode>_seed<s>/datasets/"
echo

n=0
for topo in "${TOPOLOGIES[@]}"; do
    for mode in "${MODES[@]}"; do
        for seed in "${SEEDS[@]}"; do
            n=$(( n + 1 ))
            dest="$RESULTS_DIR/${topo}_${mode}_seed${seed}/datasets"
            echo "=== [$n/$total] $topo + $mode (seed=$seed) ==="

            run_cmd=(python3 "$ROOT_DIR/light_simulation.py"
                     --topology "$topo" --routing "$mode"
                     --seed "$seed" --duration "$DURATION")

            if [[ "$DRY_RUN" -eq 1 ]]; then
                echo "  DRY-RUN: ${run_cmd[*]}"
                echo "  DRY-RUN: cp -r $DATA_DIR/datasets/. $dest/"
                continue
            fi

            "${run_cmd[@]}"

            # Butun datasets/ papkasini (CSV/parquet/metadata.json) ko'chiramiz.
            if [[ ! -d "$DATA_DIR/datasets" ]]; then
                echo "  [!] $DATA_DIR/datasets topilmadi — run dataset yaratmadi, o'tkazib yuborildi." >&2
                continue
            fi
            mkdir -p "$dest"
            cp -r "$DATA_DIR/datasets/." "$dest/"
            echo "  -> $dest"
        done
    done
done

echo
echo "Kampaniya tugadi. Birlashtirish uchun:"
echo "  python3 $RESULTS_DIR/combine_datasets.py"
