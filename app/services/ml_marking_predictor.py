import json
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score
import pickle
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
import re

class LecturerMarkingPredictor:
    """
    ML model to learn lecturer marking patterns and predict expected grades
    """
    
    def __init__(self, instructor_id: str):
        self.instructor_id = instructor_id
        self.model_file = f"marking_model_{instructor_id}.pkl"
        self.scaler_file = f"marking_scaler_{instructor_id}.pkl"
        
        # Models
        self.grade_predictor = RandomForestRegressor(n_estimators=100, random_state=42)
        self.anomaly_detector = IsolationForest(contamination=0.1, random_state=42)
        self.feedback_clusterer = KMeans(n_clusters=5, random_state=42)
        self.scaler = StandardScaler()
        
        # Pattern storage
        self.marking_patterns = {
            "assignment_type_tendencies": {},
            "feedback_style": {},
            "grading_strictness": {},
            "consistency_metrics": {}
        }
        
        self.is_trained = False
        self.load_model()
    
    def extract_features(self, data_point: Dict) -> np.array:
        """Extract numerical features from a data point"""
        features = []
        
        # Assignment features
        features.append(data_point.get("points_possible", 0))
        features.append(1 if data_point.get("late_submission", False) else 0)
        features.append(data_point.get("attempt_count", 1))
        
        # Assignment type encoding (one-hot)
        assignment_types = ["quiz", "essay", "lab", "project", "discussion", "homework", "other"]
        assignment_type = data_point.get("assignment_type", "other")
        for atype in assignment_types:
            features.append(1 if assignment_type == atype else 0)
        
        # Feedback features
        feedback_data = data_point.get("feedback_data", {})
        features.append(feedback_data.get("feedback_length", 0))
        features.append(feedback_data.get("feedback_sentiment", 0))
        features.append(1 if feedback_data.get("specific_points_mentioned", False) else 0)
        features.append(1 if feedback_data.get("improvement_suggestions", False) else 0)
        features.append(1 if feedback_data.get("positive_reinforcement", False) else 0)
        features.append(feedback_data.get("word_count", 0))
        features.append(feedback_data.get("comment_count", 0))
        
        # Assignment characteristics
        assignment_features = data_point.get("assignment_features", {})
        features.append(1 if assignment_features.get("due_date_set", False) else 0)
        features.append(1 if assignment_features.get("has_description", False) else 0)
        features.append(assignment_features.get("description_length", 0))
        features.append(assignment_features.get("allowed_attempts", 1))
        
        # Rubric features (if available)
        rubric_data = data_point.get("rubric_data")
        if rubric_data:
            features.append(rubric_data.get("criteria_count", 0))
            features.append(rubric_data.get("average_criteria_score", 0))
            features.append(rubric_data.get("score_variance", 0))
        else:
            features.extend([0, 0, 0])  # No rubric data
        
        return np.array(features)
    
    def train_model(self, data_file: str):
        """Train the model on collected marking data"""
        try:
            with open(data_file, 'r') as f:
                marking_data = json.load(f)
            
            data_points = marking_data.get("data_points", [])
            if len(data_points) < 5:
                raise ValueError("Need at least 5 data points to train model")
            
            # Extract features and targets
            X = []
            y = []
            
            for dp in data_points:
                if dp.get("percentage") is not None:
                    features = self.extract_features(dp)
                    X.append(features)
                    y.append(dp["percentage"])
            
            X = np.array(X)
            y = np.array(y)
            
            # Scale features
            X_scaled = self.scaler.fit_transform(X)
            
            # Train grade predictor
            if len(X) > 10:
                X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
                self.grade_predictor.fit(X_train, y_train)
                
                # Evaluate model
                y_pred = self.grade_predictor.predict(X_test)
                mae = mean_absolute_error(y_test, y_pred)
                r2 = r2_score(y_test, y_pred)
                
                print(f"Model trained - MAE: {mae:.2f}, R²: {r2:.3f}")
            else:
                self.grade_predictor.fit(X_scaled, y)
                print("Model trained with limited data")
            
            # Train anomaly detector
            self.anomaly_detector.fit(X_scaled)
            
            # Analyze marking patterns
            self._analyze_marking_patterns(data_points)
            
            self.is_trained = True
            self.save_model()
            
            return {
                "status": "success",
                "data_points_used": len(X),
                "model_performance": {
                    "mae": mae if len(X) > 10 else None,
                    "r2": r2 if len(X) > 10 else None
                },
                "patterns_discovered": self.marking_patterns
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def predict_expected_grade(self, assignment_data: Dict) -> Dict:
        """Predict expected grade for a new assignment"""
        if not self.is_trained:
            return {"status": "error", "message": "Model not trained yet"}
        
        try:
            features = self.extract_features(assignment_data)
            features_scaled = self.scaler.transform([features])
            
            # Predict grade
            predicted_percentage = self.grade_predictor.predict(features_scaled)[0]
            
            # Check for anomaly
            anomaly_score = self.anomaly_detector.decision_function(features_scaled)[0]
            is_anomaly = self.anomaly_detector.predict(features_scaled)[0] == -1
            
            # Calculate confidence based on similar assignments
            confidence = self._calculate_confidence(assignment_data)
            
            return {
                "status": "success",
                "predicted_percentage": round(predicted_percentage, 1),
                "predicted_score": round((predicted_percentage / 100) * assignment_data.get("points_possible", 100), 1),
                "confidence": confidence,
                "is_anomaly": is_anomaly,
                "anomaly_score": anomaly_score,
                "explanation": self._explain_prediction(assignment_data, predicted_percentage)
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def detect_grading_anomaly(self, actual_grade: float, assignment_data: Dict) -> Dict:
        """Detect if an actual grade is anomalous compared to expected patterns"""
        prediction = self.predict_expected_grade(assignment_data)
        
        if prediction["status"] != "success":
            return prediction
        
        predicted_percentage = prediction["predicted_percentage"]
        actual_percentage = (actual_grade / assignment_data.get("points_possible", 100)) * 100
        
        difference = abs(actual_percentage - predicted_percentage)
        
        # Determine if this is a significant anomaly
        threshold = self._get_anomaly_threshold(assignment_data.get("assignment_type", "other"))
        is_significant_anomaly = difference > threshold
        
        return {
            "status": "success",
            "actual_percentage": actual_percentage,
            "predicted_percentage": predicted_percentage,
            "difference": round(difference, 1),
            "is_significant_anomaly": is_significant_anomaly,
            "threshold": threshold,
            "severity": "high" if difference > threshold * 1.5 else "medium" if difference > threshold else "low",
            "explanation": self._explain_anomaly(actual_percentage, predicted_percentage, assignment_data)
        }
    
    def _analyze_marking_patterns(self, data_points: List[Dict]):
        """Analyze and store marking patterns"""
        # Assignment type tendencies
        type_scores = {}
        for dp in data_points:
            atype = dp.get("assignment_type", "other")
            if atype not in type_scores:
                type_scores[atype] = []
            if dp.get("percentage") is not None:
                type_scores[atype].append(dp["percentage"])
        
        self.marking_patterns["assignment_type_tendencies"] = {
            atype: {
                "average": np.mean(scores),
                "std": np.std(scores),
                "count": len(scores)
            } for atype, scores in type_scores.items() if len(scores) > 0
        }
        
        # Feedback style analysis
        feedback_lengths = [dp["feedback_data"]["feedback_length"] for dp in data_points]
        feedback_sentiments = [dp["feedback_data"]["feedback_sentiment"] for dp in data_points]
        
        self.marking_patterns["feedback_style"] = {
            "average_length": np.mean(feedback_lengths) if feedback_lengths else 0,
            "average_sentiment": np.mean(feedback_sentiments) if feedback_sentiments else 0,
            "gives_detailed_feedback": np.mean(feedback_lengths) > 100,
            "generally_positive": np.mean(feedback_sentiments) > 0
        }
        
        # Grading strictness
        all_scores = [dp["percentage"] for dp in data_points if dp.get("percentage") is not None]
        self.marking_patterns["grading_strictness"] = {
            "average_grade": np.mean(all_scores) if all_scores else 0,
            "grade_variance": np.var(all_scores) if all_scores else 0,
            "is_strict": np.mean(all_scores) < 75 if all_scores else False,
            "is_lenient": np.mean(all_scores) > 85 if all_scores else False
        }
    
    def _calculate_confidence(self, assignment_data: Dict) -> float:
        """Calculate confidence in prediction based on similar assignments"""
        assignment_type = assignment_data.get("assignment_type", "other")
        type_data = self.marking_patterns["assignment_type_tendencies"].get(assignment_type)
        
        if not type_data:
            return 0.3  # Low confidence for unknown assignment types
        
        # Higher confidence for assignment types with more data
        confidence = min(0.9, 0.3 + (type_data["count"] * 0.1))
        return round(confidence, 2)
    
    def _get_anomaly_threshold(self, assignment_type: str) -> float:
        """Get anomaly threshold for assignment type"""
        type_data = self.marking_patterns["assignment_type_tendencies"].get(assignment_type)
        
        if type_data and type_data["std"] > 0:
            return type_data["std"] * 2  # 2 standard deviations
        else:
            return 15.0  # Default threshold of 15%
    
    def _explain_prediction(self, assignment_data: Dict, predicted_percentage: float) -> str:
        """Explain why the model made this prediction"""
        assignment_type = assignment_data.get("assignment_type", "other")
        type_data = self.marking_patterns["assignment_type_tendencies"].get(assignment_type)
        
        if type_data:
            type_avg = type_data["average"]
            explanation = f"Based on {type_data['count']} similar {assignment_type} assignments (avg: {type_avg:.1f}%), "
        else:
            explanation = f"Based on overall grading patterns, "
        
        if predicted_percentage > 85:
            explanation += "this appears to be high-quality work."
        elif predicted_percentage > 75:
            explanation += "this appears to be good work with minor issues."
        elif predicted_percentage > 65:
            explanation += "this appears to be average work needing improvement."
        else:
            explanation += "this appears to need significant improvement."
        
        return explanation
    
    def _explain_anomaly(self, actual: float, predicted: float, assignment_data: Dict) -> str:
        """Explain the detected anomaly"""
        difference = actual - predicted
        assignment_type = assignment_data.get("assignment_type", "assignment")
        
        if difference > 0:
            return f"Grade is {abs(difference):.1f}% higher than expected for this {assignment_type}. This could indicate generous grading or exceptional work."
        else:
            return f"Grade is {abs(difference):.1f}% lower than expected for this {assignment_type}. This could indicate strict grading or potential grading error."
    
    def save_model(self):
        """Save the trained model"""
        model_data = {
            "grade_predictor": self.grade_predictor,
            "anomaly_detector": self.anomaly_detector,
            "scaler": self.scaler,
            "marking_patterns": self.marking_patterns,
            "is_trained": self.is_trained,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(self.model_file, 'wb') as f:
            pickle.dump(model_data, f)
    
    def load_model(self):
        """Load a previously trained model"""
        if os.path.exists(self.model_file):
            try:
                with open(self.model_file, 'rb') as f:
                    model_data = pickle.load(f)
                
                self.grade_predictor = model_data["grade_predictor"]
                self.anomaly_detector = model_data["anomaly_detector"]
                self.scaler = model_data["scaler"]
                self.marking_patterns = model_data["marking_patterns"]
                self.is_trained = model_data["is_trained"]
                
                print(f"Loaded existing model for instructor {self.instructor_id}")
            except Exception as e:
                print(f"Error loading model: {e}")
                self.is_trained = False
    
    def update_model(self, new_data_point: Dict):
        """Update model with new data point (online learning)"""
        # For now, we'll retrain periodically
        # In production, you might want incremental learning
        pass
    
    def get_model_stats(self) -> Dict:
        """Get statistics about the trained model"""
        return {
            "instructor_id": self.instructor_id,
            "is_trained": self.is_trained,
            "marking_patterns": self.marking_patterns,
            "model_file": self.model_file,
            "last_updated": datetime.fromtimestamp(os.path.getmtime(self.model_file)).isoformat() if os.path.exists(self.model_file) else None
        } 