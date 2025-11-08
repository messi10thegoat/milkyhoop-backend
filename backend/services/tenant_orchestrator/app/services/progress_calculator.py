"""
Progress Calculator
Calculates setup completion progress based on collected data
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class ProgressCalculator:
    """
    Calculates setup progress based on filled business data fields
    
    Features:
    - Weighted field importance
    - Completion percentage calculation
    - Progress milestones tracking
    """
    
    # Define required fields and their weights
    FIELD_WEIGHTS = {
        "business_name": 15,      # Critical
        "business_type": 15,      # Critical
        "products_services": 15,  # Critical
        "operating_hours": 15,    # Important
        "location": 15,           # Important
        "pricing": 15,            # Important
        "target_customers": 10    # Optional
    }
    
    @staticmethod
    def calculate_progress(business_data: Dict[str, Any]) -> int:
        """
        Calculate completion progress percentage
        
        Args:
            business_data: Dictionary containing business information
            
        Returns:
            Progress percentage (0-100)
        """
        total_weight = sum(ProgressCalculator.FIELD_WEIGHTS.values())
        earned_weight = 0
        
        for field, weight in ProgressCalculator.FIELD_WEIGHTS.items():
            value = business_data.get(field)
            
            # Check if field has meaningful data
            if ProgressCalculator._is_field_filled(value):
                earned_weight += weight
                logger.debug(f"Field '{field}' is filled, adding {weight} points")
        
        progress = int((earned_weight / total_weight) * 100)
        logger.info(f"Progress calculated: {progress}% ({earned_weight}/{total_weight} points)")
        
        return progress
    
    @staticmethod
    def _is_field_filled(value: Any) -> bool:
        """
        Check if field has meaningful data
        
        Args:
            value: Field value to check
            
        Returns:
            True if field is filled with meaningful data
        """
        if value is None:
            return False
        
        if isinstance(value, str):
            return len(value.strip()) > 0
        
        if isinstance(value, list):
            return len(value) > 0
        
        if isinstance(value, dict):
            return len(value) > 0
        
        return bool(value)
    
    @staticmethod
    def get_completion_status(progress: int) -> str:
        """
        Get human-readable completion status
        
        Args:
            progress: Progress percentage
            
        Returns:
            Status string
        """
        if progress >= 100:
            return "complete"
        elif progress >= 70:
            return "almost_complete"
        elif progress >= 40:
            return "in_progress"
        else:
            return "just_started"
    
    @staticmethod
    def get_next_milestone(progress: int) -> Dict[str, Any]:
        """
        Get next progress milestone information
        
        Args:
            progress: Current progress percentage
            
        Returns:
            Dictionary with next milestone info
        """
        milestones = [
            {"threshold": 30, "message": "Info dasar udah ada! 👍"},
            {"threshold": 60, "message": "Setengah jalan! Hampir selesai 🎯"},
            {"threshold": 90, "message": "Tinggal sedikit lagi! 🚀"},
            {"threshold": 100, "message": "Lengkap! Siap bikin chatbot 🎉"}
        ]
        
        for milestone in milestones:
            if progress < milestone["threshold"]:
                return {
                    "next_threshold": milestone["threshold"],
                    "message": milestone["message"],
                    "remaining": milestone["threshold"] - progress
                }
        
        return {
            "next_threshold": 100,
            "message": "Setup complete!",
            "remaining": 0
        }
