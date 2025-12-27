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
    allow_origins=["*"],  # Allows all origins (React, Mobile, Postman, etc.)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, DELETE)
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
    We exclude the raw binary photo data from the general JSON response to keep it light.
    """
    return {
        "id": str(player["_id"]),
        "name": player["name"],
        "dob": player["dob"],
        "instagram_link": player["instagram_link"],
        "facebook_link": player["facebook_link"],
        "photo_url": f"/players/{str(player['_id'])}/photo"  # Link to fetch image
    }

# --- Pydantic Models (For Documentation) ---
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
        "photo_data": Binary(photo_content),  # Store binary data
        "photo_content_type": photo.content_type
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

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)