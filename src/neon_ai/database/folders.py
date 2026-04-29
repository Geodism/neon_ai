import os
import re

MASTER_DRIVE = "D:/Projects"

def clean_name(name):
    """Safely removes illegal characters for Windows folders."""
    if not name: return ""
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()

def get_target_folder(street_num, street_name, site_name, estimate_id, category=None):
    num = clean_name(street_num)
    street = clean_name(street_name)
    site = clean_name(site_name)
    
    # Combine the address part and strip whitespace
    address_part = f"{num} {street}".strip()
    
    # --- Fallback Logic: The "Project Person" Safety Net ---
    if address_part and site:
        project_folder = f"{address_part} - {site}"
    elif address_part:
        project_folder = address_part
    elif site:
        project_folder = site
    else:
        # If everything is empty, use a generic name so the app doesn't crash
        project_folder = f"Project_ID_{estimate_id}"
        
    # Build the path
    if category:
        target = os.path.join(MASTER_DRIVE, project_folder, str(estimate_id), category)
    else:
        target = os.path.join(MASTER_DRIVE, project_folder, str(estimate_id))
    
    target_path = target.replace("\\", "/")
    
    # CHECK: Does the D: drive actually exist?
    drive = os.path.splitdrive(target_path)[0]
    if not os.path.exists(drive):
        # Fallback to C: if D: is missing
        target_path = target_path.replace("D:", "C:")
        os.makedirs(target_path, exist_ok=True)
    else:
        os.makedirs(target_path, exist_ok=True)
    
    return target_path
