# Methodology

This evaluation covers 15 scans: five recorded seeds (42–46) for each of
three lighting settings (`ring_light`, `daylight`, and `tube_light`). The
results CSV contains one row per scan, with overall accuracy, per-class
precision and recall, and length mean absolute error (MAE). The accompanying
figures identify the scans and lighting conditions as simulated. Accordingly,
the lighting and scan results reported here are simulated; they are not
measurements from a physical camera or lighting box. The numerical outcomes
are computed experiment results, but the CSV and figures do not establish
performance on physical rice samples.

Accuracy is reported as the overall fraction of correct classifications.
For each class, precision is the fraction of predictions for that class that
are correct, and recall is the fraction of actual members of that class that
are identified correctly. Length MAE is the average absolute difference
between predicted and reference lengths, in millimetres. Lighting summaries
below are arithmetic means across the five seed-level rows. The accuracy
chart shows the mean with sample standard-deviation error bars across those
five runs; the per-class recall chart shows the mean recall by class and
lighting.

The supplied CSV does not record the grain-matching procedure, the number of
grains used in each scan, or the provenance and composition of training data.
Those details therefore cannot be independently assessed from these results
and are not assumed here.

# Validation

The overall mean accuracy across all 15 scans was
0.42083333333333334. Ring lighting had the highest mean accuracy in these
runs (0.4425), followed by tube lighting (0.42999999999999988) and daylight
(0.38999999999999996). Table values below are calculated from the five rows
for each lighting condition. Accuracy standard deviations are sample
standard deviations; class precision, class recall, and length MAE are
arithmetic means.

| Simulated lighting | Accuracy mean | Accuracy sample SD | Length MAE mean (mm) | Good P / R | Broken P / R | Chalky P / R | Discolored P / R |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ring_light` | 0.4425 | 0.033773140215265708 | 0.14216999999999999 | 1 / 0.16662141779788836 | 1 / 1 | 0.16904428904428898 / 1 | 1 / 1 |
| `daylight` | 0.38999999999999996 | 0.016298006013006683 | 0.14295749999999999 | 1 / 0.087432545667839742 | 1 / 1 | 0.15668482283722329 / 1 | 1 / 1 |
| `tube_light` | 0.42999999999999988 | 0.03137475099502781 | 0.13846249999999999 | 0.97142857142857153 / 0.15159879336349921 | 1 / 1 | 0.16650943396226411 / 1 | 1 / 0.95999999999999996 |

P / R denotes mean precision / mean recall across the five seeds. The results
show a marked difference between classes: broken and chalky recall was 1.0
for every lighting condition, while mean good-grain recall was between
0.087432545667839742 and 0.16662141779788836. Mean chalky precision was
between 0.15668482283722329 and 0.16904428904428898. Thus, perfect recall
for some classes did not correspond to high overall accuracy. The mean
length MAE ranged from 0.13846249999999999 mm to 0.14295749999999999 mm.

The accuracy chart is available at
[`data/accuracy_by_lighting.png`](../data/accuracy_by_lighting.png), and the
per-class recall chart is at
[`data/per_class_recall.png`](../data/per_class_recall.png). The underlying
per-scan measurements are in [`validation/results.csv`](../validation/results.csv).

**Limitations.** This is a small experiment of only 15 simulated scans and
five seeds per lighting condition. Simulated camera and lighting results do
not demonstrate performance with physical hardware or real-world variation
in rice samples. The supplied results do not document training-data
provenance; if the model was trained on synthetic data, that may further
limit generalisation to real grain samples, but its effect cannot be
quantified from this CSV and these charts. The CSV also omits details of
grain matching and scan composition, limiting independent reproduction and
interpretation of the reported metrics.
