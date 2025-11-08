"""
Data Cleaning Module for Business Setup
Cleans extracted business data to remove common user input artifacts
"""

from typing import Dict, Any, List
import re


class DataCleaner:
    """
    Cleans extracted business data from natural language input
    Removes common conversational artifacts and normalizes data
    """
    
    @classmethod
    def clean_business_name(cls, business_name: str) -> str:
        """
        Clean business_name field
        Remove common prefixes like "namanya", "namany", "nama bisnis"
        
        Examples:
            "namany kopi Barokah" -> "Kopi Barokah"
            "nama bisnis saya adalah Warung Makan" -> "Warung Makan"
        """
        if not business_name:
            return ""
        
        text = business_name.strip()
        
        # Remove common prefixes (case insensitive)
        prefixes_to_remove = [
            "nama bisnis saya adalah",
            "nama bisnisnya adalah",
            "nama bisnis adalah",
            "namanya adalah",
            "nama saya adalah",
            "nama bisnis",
            "namanya",
            "namany",
            "nama",
        ]
        
        text_lower = text.lower()
        for prefix in prefixes_to_remove:
            if text_lower.startswith(prefix):
                # Remove prefix and strip spaces
                text = text[len(prefix):].strip()
                text_lower = text.lower()
        
        # Capitalize first letter of each word for consistency
        text = ' '.join(word.capitalize() for word in text.split())
        
        return text
    
    @classmethod
    def clean_pricing(cls, pricing: str) -> str:
        """
        Clean pricing_info field
        Remove common prefixes like "harganya", "harga", "mulai dari"
        
        Examples:
            "harganya 20-50rb" -> "20-50rb"
            "mulai dari 15-35rb" -> "15-35rb"
        """
        if not pricing:
            return ""
        
        text = pricing.strip()
        
        # Remove common prefixes
        prefixes_to_remove = [
            "harga produk kami adalah",
            "harganya mulai dari",
            "harga mulai dari",
            "mulai dari harga",
            "harganya adalah",
            "mulai dari",
            "harganya",
            "harga",
        ]
        
        text_lower = text.lower()
        for prefix in prefixes_to_remove:
            if text_lower.startswith(prefix):
                text = text[len(prefix):].strip()
                text_lower = text.lower()
        
        return text
    
    @classmethod
    def clean_operating_hours(cls, hours: str) -> str:
        """
        Clean operating_hours field
        Remove common prefixes like "jam", "buka jam", "jam buka"
        
        Examples:
            "jam 8 pagi sampai 10 malem" -> "8 pagi sampai 10 malem"
            "buka jam 09:00-21:00" -> "09:00-21:00"
            "jam operasional 08.00-22.00" -> "08.00-22.00"
        """
        if not hours:
            return ""
        
        text = hours.strip()
        
        # Remove common prefixes
        prefixes_to_remove = [
            "jam operasional dari",
            "jam operasional adalah",
            "jam operasionalnya",
            "jam operasional",
            "buka dari jam",
            "buka jam",
            "jam buka",
            "jam ",  # Simple "jam " prefix
        ]
        
        text_lower = text.lower()
        for prefix in prefixes_to_remove:
            if text_lower.startswith(prefix):
                text = text[len(prefix):].strip()
                text_lower = text.lower()
        
        return text
    
    @classmethod
    def clean_location(cls, location: str) -> str:
        """
        Clean location field
        Remove common prefixes like "lokasinya", "lokasi saya", "tempatnya di"
        
        Examples:
            "lokasinya di Jakarta" -> "Jakarta"
            "lokasi saya ada di Bandung" -> "Bandung"
        """
        if not location:
            return ""
        
        text = location.strip()
        
        # Remove common prefixes
        prefixes_to_remove = [
            "lokasi saya ada di",
            "lokasinya ada di",
            "lokasi kami di",
            "tempatnya di",
            "lokasinya di",
            "lokasi saya",
            "lokasinya",
            "lokasi di",
            "lokasi",
            "di ",
        ]
        
        text_lower = text.lower()
        for prefix in prefixes_to_remove:
            if text_lower.startswith(prefix):
                # Remove prefix, preserve original case
                text = text[len(prefix):].strip()
                text_lower = text.lower()
        
        # Capitalize first letter of each word for consistency
        if text:
            text = ' '.join(word.capitalize() for word in text.split())
        
        return text
    
    @classmethod
    def clean_products_services(cls, products: List[str]) -> List[str]:
        """
        Clean products_services list
        Remove duplicates, normalize case, filter empty strings
        
        Examples:
            ["Kopi", "kopi", "Teh", ""] -> ["kopi", "teh"]
        """
        if not products:
            return []
        
        # Normalize to lowercase and strip
        cleaned = [p.strip().lower() for p in products if p and p.strip()]
        
        # Remove duplicates while preserving order
        seen = set()
        result = []
        for item in cleaned:
            if item not in seen:
                seen.add(item)
                result.append(item)
        
        return result

    @classmethod
    def clean_target_customers(cls, target_customers: str) -> str:
        """
        Clean target_customers field
        Remove common prefixes and normalize
        
        Examples:
            "target saya adalah mahasiswa" -> "Mahasiswa"
            "customer kami anak muda" -> "Anak muda"
        """
        if not target_customers:
            return ""
        
        text = target_customers.strip()
        
        # Remove common prefixes
        prefixes_to_remove = [
            "target customer saya adalah",
            "target customer saya",
            "target saya adalah",
            "target customer adalah",
            "customer saya adalah",
            "target customer",
            "target saya",
            "customer kami",
            "target kami",
            "targetnya",
            "target",
        ]
        
        text_lower = text.lower()
        for prefix in prefixes_to_remove:
            if text_lower.startswith(prefix):
                # Remove prefix, keep proper case from remaining text
                text = text[len(prefix):].strip()
                text_lower = text.lower()
        
        # Capitalize first letter for consistency
        if text:
            text = text[0].upper() + text[1:]
        
        return text
    
    @classmethod
    def clean_all_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply all cleaning rules to extracted data
        
        Args:
            data: Raw extracted data dictionary
            
        Returns:
            Cleaned data dictionary
        """
        cleaned = data.copy()
        
        # Clean business_name
        if "business_name" in cleaned and cleaned["business_name"]:
            cleaned["business_name"] = cls.clean_business_name(cleaned["business_name"])
        
        # Clean pricing_info
        if "pricing_info" in cleaned and cleaned["pricing_info"]:
            cleaned["pricing_info"] = cls.clean_pricing(cleaned["pricing_info"])
        
        # Clean operating_hours (NEW)
        if "operating_hours" in cleaned and cleaned["operating_hours"]:
            cleaned["operating_hours"] = cls.clean_operating_hours(cleaned["operating_hours"])
        
        # Clean target_customers
        if "target_customers" in cleaned and cleaned["target_customers"]:
            cleaned["target_customers"] = cls.clean_target_customers(cleaned["target_customers"])
        
        # Clean products_services (list)
        if "products_services" in cleaned and cleaned["products_services"]:
            if isinstance(cleaned["products_services"], list):
                cleaned["products_services"] = cls.clean_products_services(cleaned["products_services"])
        
        # Clean location (ENHANCED)
        if "location" in cleaned and cleaned["location"]:
            cleaned["location"] = cls.clean_location(cleaned["location"])
        
        return cleaned