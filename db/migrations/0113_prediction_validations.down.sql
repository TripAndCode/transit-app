-- DESTRUCTIVE: drops prediction_validations and every validation recorded;
-- recording one again needs its report from scripts/validate_vp_predictions.py.
DROP TABLE IF EXISTS prediction_validations;
