"""
Apollo Auto-Updater
Checks GitHub Releases for new versions and updates frontend files
"""
import os
import sys
import json
import shutil
import zipfile
import requests
from pathlib import Path
from packaging import version as version_parser

# GitHub repository info
GITHUB_REPO = "byte-bit06/test"  # Change this to your GitHub repo
CURRENT_VERSION = "1.0.2"  # Update this with each release

class ApolloUpdater:
    def __init__(self):
        self.user_dir = Path(__file__).parent
        self.project_root = self.user_dir.parent
        self.temp_dir = self.user_dir / "temp_update"
        
    def check_for_updates(self):
        """Check GitHub Releases for newer version"""
        try:
            print("🔍 Checking for updates...")
            
            # GitHub API endpoint for latest release
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            
            response = requests.get(api_url, timeout=10)
            if response.status_code != 200:
                print(f"⚠️  Could not check for updates (status {response.status_code})")
                return None
            
            release_data = response.json()
            latest_version = release_data.get("tag_name", "").lstrip("v")
            
            if not latest_version:
                print("⚠️  No version found in release")
                return None
            
            print(f"📦 Current version: {CURRENT_VERSION}")
            print(f"📦 Latest version:  {latest_version}")
            
            # Compare versions
            if version_parser.parse(latest_version) > version_parser.parse(CURRENT_VERSION):
                return {
                    "version": latest_version,
                    "download_url": self._get_download_url(release_data),
                    "release_notes": release_data.get("body", ""),
                    "published_at": release_data.get("published_at", ""),
                }
            else:
                print("✅ You're running the latest version!")
                return None
                
        except Exception as e:
            print(f"❌ Error checking for updates: {e}")
            return None
    
    def _get_download_url(self, release_data):
        """Get download URL for frontend update"""
        assets = release_data.get("assets", [])
        
        # Look for frontend-update.zip
        for asset in assets:
            if asset.get("name") == "apollo-frontend-update.zip":
                return asset.get("browser_download_url")
        
        # Fallback to zipball (entire repo)
        return release_data.get("zipball_url")
    
    def download_update(self, download_url):
        """Download update package"""
        try:
            print(f"⬇️  Downloading update from {download_url}...")
            
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Save to temp file
            temp_file = self.user_dir / "update.zip"
            total_size = int(response.headers.get('content-length', 0))
            
            with open(temp_file, 'wb') as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"   Progress: {percent:.1f}%", end='\r')
            
            print("\n✅ Download complete!")
            return temp_file
            
        except Exception as e:
            print(f"❌ Error downloading update: {e}")
            return None
    
    def backup_current_version(self):
        """Backup current user/ folder"""
        try:
            backup_dir = self.project_root / "user_backup"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            
            print("💾 Creating backup...")
            shutil.copytree(
                self.user_dir, 
                backup_dir,
                ignore=shutil.ignore_patterns(
                    '__pycache__', '*.pyc', '*.db', 
                    'temp_update', 'update.zip', '.apollo'
                )
            )
            print(f"✅ Backup created at: {backup_dir}")
            return backup_dir
            
        except Exception as e:
            print(f"❌ Error creating backup: {e}")
            return None
    
    def apply_update(self, update_file):
        """Extract and apply update"""
        try:
            print("📦 Extracting update...")
            
            # Create temp directory
            self.temp_dir.mkdir(exist_ok=True)
            
            # Extract ZIP
            with zipfile.ZipFile(update_file, 'r') as zip_ref:
                zip_ref.extractall(self.temp_dir)
            
            # Find the user/ folder in the extracted files
            user_folder = None
            for root, dirs, files in os.walk(self.temp_dir):
                if 'user' in dirs or Path(root).name == 'user':
                    user_folder = Path(root) / 'user' if 'user' in dirs else Path(root)
                    break
            
            if not user_folder or not user_folder.exists():
                print("❌ Could not find user/ folder in update package")
                return False
            
            print("🔄 Applying update...")
            
            # Files to preserve (user data)
            preserve_patterns = [
                '__pycache__',
                '*.pyc',
                '*.db',
                'spotify_cookies.json',
                '.apollo',
            ]
            
            # Copy new files
            for item in user_folder.iterdir():
                if item.name in ['__pycache__', 'temp_update', 'update.zip']:
                    continue
                
                dest = self.user_dir / item.name
                
                if item.is_file():
                    shutil.copy2(item, dest)
                    print(f"   Updated: {item.name}")
                elif item.is_dir():
                    # For directories, merge instead of replace
                    if dest.exists():
                        # Update files in existing directory
                        for subitem in item.rglob('*'):
                            if subitem.is_file():
                                rel_path = subitem.relative_to(item)
                                dest_file = dest / rel_path
                                dest_file.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(subitem, dest_file)
                    else:
                        shutil.copytree(item, dest)
                    print(f"   Updated: {item.name}/")
            
            # Cleanup
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            update_file.unlink(missing_ok=True)
            
            print("✅ Update applied successfully!")
            print("\n🔄 Please restart Apollo to use the new version")
            return True
            
        except Exception as e:
            print(f"❌ Error applying update: {e}")
            return False
    
    def restore_backup(self, backup_dir):
        """Restore from backup if update fails"""
        try:
            print("⚠️  Restoring from backup...")
            
            # Remove current user folder
            for item in self.user_dir.iterdir():
                if item.name not in ['__pycache__', 'temp_update']:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
            
            # Restore from backup
            for item in backup_dir.iterdir():
                dest = self.user_dir / item.name
                if item.is_file():
                    shutil.copy2(item, dest)
                else:
                    shutil.copytree(item, dest)
            
            print("✅ Backup restored successfully")
            return True
            
        except Exception as e:
            print(f"❌ Error restoring backup: {e}")
            return False
    
    def run_update(self, auto=False):
        """Main update process"""
        print("=" * 60)
        print("🚀 Apollo Auto-Updater")
        print("=" * 60)
        print()
        
        # Check for updates
        update_info = self.check_for_updates()
        
        if not update_info:
            return
        
        # Show release notes
        print()
        print("📝 Release Notes:")
        print("-" * 60)
        print(update_info["release_notes"] or "No release notes available")
        print("-" * 60)
        print()
        
        # Ask for confirmation if not auto
        if not auto:
            response = input(f"Update to v{update_info['version']}? (y/n): ").lower()
            if response != 'y':
                print("❌ Update cancelled")
                return
        
        # Backup
        backup_dir = self.backup_current_version()
        if not backup_dir:
            print("❌ Update cancelled - backup failed")
            return
        
        # Download
        update_file = self.download_update(update_info["download_url"])
        if not update_file:
            print("❌ Update cancelled - download failed")
            return
        
        # Apply
        success = self.apply_update(update_file)
        
        if not success:
            print("\n⚠️  Update failed! Restoring backup...")
            self.restore_backup(backup_dir)
        else:
            # Update version in this file
            self._update_version_in_file(update_info["version"])
        
        print()
        print("=" * 60)
    
    def _update_version_in_file(self, new_version):
        """Update CURRENT_VERSION in this file"""
        try:
            updater_file = Path(__file__)
            content = updater_file.read_text()
            
            # Replace version
            new_content = content.replace(
                f'CURRENT_VERSION = "{CURRENT_VERSION}"',
                f'CURRENT_VERSION = "{new_version}"'
            )
            
            updater_file.write_text(new_content)
            print(f"✅ Version updated to {new_version}")
        except Exception as e:
            print(f"⚠️  Could not update version in file: {e}")


def main():
    """Run updater"""
    updater = ApolloUpdater()
    
    # Check if running in auto mode
    auto = "--auto" in sys.argv or "-a" in sys.argv
    
    updater.run_update(auto=auto)
    
    if not auto:
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
