import os
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from bson import ObjectId, Binary
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uvicorn
from dotenv import load_dotenv

app = FastAPI()

load_dotenv()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Get the full URI directly from the environment
MONGO_URI = os.getenv("MONGO_URI")
client = AsyncIOMotorClient(MONGO_URI)
db = client.player_db
collection = db.players

# --- Helper Functions ---
def player_helper(player) -> dict:
    """
    Converts MongoDB document to a JSON-friendly dictionary.
    Now includes Stats and automatically calculates Unbeaten %.
    """
    # 1. Fetch Stats (Default to 0 if not present)
    matches = player.get("matches_played", 0)
    wins = player.get("wins", 0)
    draws = player.get("draws", 0)
    loss = player.get("loss", 0)

    # 2. Calculate Unbeaten % (Read-Only Logic)
    # Formula: (Wins + Draws) / Total Matches * 100
    if matches > 0:
        non_losing_games = wins + draws
        unbeaten_pct = round((non_losing_games / matches) * 100, 2)
    else:
        unbeaten_pct = 0.0

    return {
        "id": str(player["_id"]),
        "name": player["name"],
        "dob": player["dob"],
        "instagram_link": player["instagram_link"],
        "facebook_link": player["facebook_link"],
        "photo_url": f"/players/{str(player['_id'])}/photo",
        
        # New Stats Fields
        "matches_played": matches,
        "wins": wins,
        "draws": draws,
        "loss": loss,
        "unbeaten_percentage": unbeaten_pct
    }

# --- Pydantic Models (For Documentation) ---
# Note: This is mainly for Swagger UI documentation. 
# The actual update logic is handled in the route parameters.
class PlayerUpdate(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None
    instagram_link: Optional[str] = None
    facebook_link: Optional[str] = None

# --- Routes ---

@app.get("/")
async def root():
    return {"message": "Player Management API is running"}

# 1. ADD PLAYER (Create)
@app.post("/players/")
async def add_player(
    name: str = Form(...),
    dob: str = Form(...),
    instagram_link: str = Form(...),
    facebook_link: str = Form(...),
    photo: UploadFile = File(...)
):
    # Read file content and convert to Binary
    photo_content = await photo.read()
    
    player_data = {
        "name": name,
        "dob": dob,
        "instagram_link": instagram_link,
        "facebook_link": facebook_link,
        "photo_data": Binary(photo_content),
        "photo_content_type": photo.content_type,
        
        # Initialize Stats to 0
        "matches_played": 0,
        "wins": 0,
        "draws": 0,
        "loss": 0
    }
    
    new_player = await collection.insert_one(player_data)
    created_player = await collection.find_one({"_id": new_player.inserted_id})
    return player_helper(created_player)

# 2. FETCH ALL PLAYERS (Read)
@app.get("/players/")
async def get_all_players():
    players = []
    async for player in collection.find():
        players.append(player_helper(player))
    return players

# 3. FETCH SINGLE PLAYER DETAILS (Read)
@app.get("/players/{id}")
async def get_player(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
        
    player = await collection.find_one({"_id": ObjectId(id)})
    if player:
        return player_helper(player)
    raise HTTPException(status_code=404, detail="Player not found")

# 4. FETCH PLAYER PHOTO (Read Image)
@app.get("/players/{id}/photo")
async def get_player_photo(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    player = await collection.find_one({"_id": ObjectId(id)})
    if player and "photo_data" in player:
        return Response(content=player["photo_data"], media_type=player["photo_content_type"])
    
    raise HTTPException(status_code=404, detail="Photo not found")

# 5. UPDATE PLAYER DETAILS (Update)
@app.put("/players/{id}")
async def update_player(
    id: str,
    name: Optional[str] = Form(None),
    dob: Optional[str] = Form(None),
    instagram_link: Optional[str] = Form(None),
    facebook_link: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None)
):
    """
    Updates player profile info. 
    NOTE: Match stats (wins, loss, etc.) are NOT accessible here, 
    so they cannot be changed accidentally during a profile update.
    """
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")

    update_data = {}
    if name: update_data["name"] = name
    if dob: update_data["dob"] = dob
    if instagram_link: update_data["instagram_link"] = instagram_link
    if facebook_link: update_data["facebook_link"] = facebook_link
    
    if photo:
        content = await photo.read()
        update_data["photo_data"] = Binary(content)
        update_data["photo_content_type"] = photo.content_type

    if not update_data:
        raise HTTPException(status_code=400, detail="No data provided for update")

    result = await collection.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    
    if result.modified_count == 1:
        updated_player = await collection.find_one({"_id": ObjectId(id)})
        return player_helper(updated_player)
    
    raise HTTPException(status_code=404, detail="Player not found or no changes made")

# 6. DELETE PLAYER (Delete)
@app.delete("/players/{id}")
async def delete_player(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
        
    delete_result = await collection.delete_one({"_id": ObjectId(id)})
    
    if delete_result.deleted_count == 1:
        return {"message": "Player deleted successfully"}
    
    raise HTTPException(status_code=404, detail="Player not found")
# ... existing imports ...

@app.get("/fix-database-schema")
async def fix_database_schema():
    """
    One-time script to add stats fields to all existing players.
    """
    # This query finds players who DO NOT have the 'matches_played' field yet
    filter_query = {"matches_played": {"$exists": False}}
    
    # This update sets the default values
    update_query = {
        "$set": {
            "matches_played": 0,
            "wins": 0,
            "draws": 0,
            "loss": 0
        }
    }
    
    result = await collection.update_many(filter_query, update_query)
    
    return {
        "message": "Database migration complete",
        "matched_count": result.matched_count,
        "modified_count": result.modified_count
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)