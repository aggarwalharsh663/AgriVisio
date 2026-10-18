simulator.make_scene(seed:int, n_grains:int, lighting:str, out_dir:str) -> {"photo_path","ground_truth_path","mm_per_pixel","card_patches":[[x,y,size]],"card_true_rgb":[[r,g,b]]}
  lighting: ring_light|daylight|tube_light. Saves out_dir/photo.jpg + ground_truth.json
  ground_truth.json: {"grains":[{"id","label","true_length_mm","true_width_mm","center":[x,y]}]}; label: good|broken|chalky|discolored
calibration.calibrate(photo_path, card_patches, card_true_rgb, out_path) -> {"corrected_path","patch_error_before","patch_error_after"}
grain_analysis.analyze_image(image_path, mm_per_pixel, output_dir) -> {"annotated_image_path","grains":[{"id","bbox":[x,y,w,h],"center":[cx,cy],"length_mm","width_mm","lb_ratio","mean_rgb":[r,g,b],"chalk_fraction","label","confidence","low_confidence","reason"}]}  (sorted left to right)
validation/validate.py: compare(ground_truth_json_path, grains) -> {"accuracy","per_class":{label:{"precision","recall"}},"confusion_matrix","labels":[good,broken,chalky,discolored],"length_mae_mm"}
API (backend/main.py): POST /api/scan {"lighting","n_grains","seed","sample_name","variety"} -> batch JSON; GET /api/batches; GET /api/batches/{id}; GET /api/health
Batch JSON: {"batch_id":"AS-YYYYMMDD-001","created_at","sample_name","variety","lighting","simulated":true,
 "images":{"raw","calibrated","annotated"} (URLs under /files/),
 "calibration":{"patch_error_before","patch_error_after"},
 "grains":[...as analyze_image...],
 "summary":{"total_grains","counts":{label:n},"percent":{label:pct},"mean_length_mm","std_length_mm","mean_width_mm","mean_lb_ratio","mean_confidence","low_confidence_count"},
 "fingerprint":{"vector":[...],"hash":"sha256"},
 "validation":{...as compare...}}
