import os
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from bson import ObjectId, Binary
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Literal
import uvicorn
from dotenv import load_dotenv

app = FastAPI()

load_dotenv()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.getenv("MONGO_URI")
client = AsyncIOMotorClient(MONGO_URI)
db = client.player_db

# Collections
collection = db.players
tournaments_collection = db.tournaments
teams_collection = db.teams
matches_collection = db.matches

# --- Helper Functions ---
def player_helper(player) -> dict:
    """
    Converts MongoDB document to a JSON-friendly dictionary.
    Includes Stats and automatically calculates Unbeaten %.
    """
    matches = player.get("matches_played", 0)
    wins = player.get("wins", 0)
    draws = player.get("draws", 0)
    loss = player.get("loss", 0)

    if matches > 0:
        non_losing_games = wins + draws
        unbeaten_pct = round((non_losing_games / matches) * 100, 2)
    else:
        unbeaten_pct = 0.0

    return {
        "id": str(player["_id"]),
        "name": player["name"],
        "dob": player["dob"],
        "instagram_link": player.get("instagram_link"),
        "facebook_link": player.get("facebook_link"),
        "photo_url": f"/players/{str(player['_id'])}/photo",
        "matches_played": matches,
        "wins": wins,
        "draws": draws,
        "loss": loss,
        "unbeaten_percentage": unbeaten_pct
    }

# --- Pydantic Models ---

# 1. PLAYER MODELS
class PlayerUpdate(BaseModel):
    name: Optional[str] = None
    dob: Optional[str] = None
    instagram_link: Optional[str] = None
    facebook_link: Optional[str] = None

# 2. TOURNAMENT MODELS
class TournamentCreate(BaseModel):
    name: str
    total_teams: int

# 3. TEAM MODELS
class TeamCreate(BaseModel):
    name: str
    tournament_id: str
    player_ids: List[str]  # List of Player IDs to add to this team

# 4. MATCH MODELS
class PlayerMatchResult(BaseModel):
    player_id: str
    # 'sub' means they were in squad but didn't play (stats don't count)
    result: Literal['win', 'loss', 'draw', 'sub']

class MatchCreate(BaseModel):
    tournament_id: str
    team_id: str
    opponent_name: str
    player_results: List[PlayerMatchResult]


# --- ROUTES ---

@app.get("/")
async def root():
    return {"message": "Player & Tournament API is running"}

# ===========================
#      PLAYER MANAGEMENT
# ===========================

@app.post("/players/")
async def add_player(
    name: str = Form(...),
    dob: str = Form(...),
    instagram_link: str = Form(...),
    facebook_link: str = Form(...),
    photo: UploadFile = File(...)
):
    photo_content = await photo.read()
    
    player_data = {
        "name": name,
        "dob": dob,
        "instagram_link": instagram_link,
        "facebook_link": facebook_link,
        "photo_data": Binary(photo_content),
        "photo_content_type": photo.content_type,
        "matches_played": 0, "wins": 0, "draws": 0, "loss": 0
    }
    
    new_player = await collection.insert_one(player_data)
    created_player = await collection.find_one({"_id": new_player.inserted_id})
    return player_helper(created_player)

@app.get("/players/")
async def get_all_players():
    players = []
    async for player in collection.find():
        players.append(player_helper(player))
    return players

@app.get("/players/{id}")
async def get_player(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    player = await collection.find_one({"_id": ObjectId(id)})
    if player: return player_helper(player)
    raise HTTPException(status_code=404, detail="Player not found")

@app.get("/players/{id}/photo")
async def get_player_photo(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    player = await collection.find_one({"_id": ObjectId(id)})
    if player and "photo_data" in player:
        return Response(content=player["photo_data"], media_type=player["photo_content_type"])
    raise HTTPException(status_code=404, detail="Photo not found")

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
        raise HTTPException(status_code=400, detail="No data provided")

    await collection.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    updated_player = await collection.find_one({"_id": ObjectId(id)})
    return player_helper(updated_player)

@app.delete("/players/{id}")
async def delete_player(id: str):
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    delete_result = await collection.delete_one({"_id": ObjectId(id)})
    if delete_result.deleted_count == 1:
        return {"message": "Player deleted"}
    raise HTTPException(status_code=404, detail="Player not found")


# ===========================
#    TOURNAMENT MANAGEMENT
# ===========================

# 1. Create Tournament
@app.post("/tournaments/")
async def create_tournament(tournament: TournamentCreate):
    t_data = tournament.dict()
    new_t = await tournaments_collection.insert_one(t_data)
    return {"message": "Tournament created", "id": str(new_t.inserted_id)}

# 2. Get All Tournaments
@app.get("/tournaments/")
async def get_tournaments():
    tournaments = []
    async for t in tournaments_collection.find():
        t["id"] = str(t["_id"])
        del t["_id"]
        tournaments.append(t)
    return tournaments

# ===========================
#      TEAM MANAGEMENT
# ===========================

# 3. Add Team to Tournament (Initialize Stats)
@app.post("/teams/")
async def add_team(team: TeamCreate):
    # Verify Tournament exists
    if not await tournaments_collection.find_one({"_id": ObjectId(team.tournament_id)}):
        raise HTTPException(status_code=404, detail="Tournament not found")
    
    # Prepare Player List with Initial Tournament Stats
    team_players = []
    for pid in team.player_ids:
        if not ObjectId.is_valid(pid):
             raise HTTPException(status_code=400, detail=f"Invalid Player ID format: {pid}")

        p = await collection.find_one({"_id": ObjectId(pid)})
        if not p:
            raise HTTPException(status_code=400, detail=f"Invalid Player ID: {pid}")
            
        team_players.append({
            "player_id": str(p["_id"]),
            "name": p["name"],
            # These stats are specific to THIS tournament/team
            "stats": {"matches_played": 0, "wins": 0, "draws": 0, "loss": 0}
        })

    team_data = {
        "name": team.name,
        "tournament_id": team.tournament_id,
        # These stats are for the TEAM itself
        "stats": {"matches_played": 0, "wins": 0, "draws": 0, "loss": 0, "points": 0},
        "players": team_players
    }

    new_team = await teams_collection.insert_one(team_data)
    return {"message": "Team added", "id": str(new_team.inserted_id)}

# 4. Get Teams for a Tournament
@app.get("/tournaments/{tournament_id}/teams")
async def get_tournament_teams(tournament_id: str):
    teams = []
    async for team in teams_collection.find({"tournament_id": tournament_id}):
        team["id"] = str(team["_id"])
        del team["_id"]
        teams.append(team)
    return teams


# ===========================
#      MATCH MANAGEMENT
# ===========================

# 5. Record Match & Auto-Update Stats
@app.post("/matches/")
async def record_match(match: MatchCreate):
    """
    1. Calculates Team Win/Loss based on player results.
    2. Updates Team Stats (Points/Wins).
    3. Updates Global Player Stats (Career).
    4. Updates Tournament Player Stats (Specific to this team).
    """
    
    # --- A. Calculate Team Outcome ---
    match_wins = 0
    match_losses = 0
    
    for p in match.player_results:
        if p.result == 'win': match_wins += 1
        elif p.result == 'loss': match_losses += 1
            
    # Logic: More wins than losses = Team Win
    team_result = "draw"
    points_awarded = 1
    
    if match_wins > match_losses:
        team_result = "win"
        points_awarded = 3
    elif match_losses > match_wins:
        team_result = "loss"
        points_awarded = 0

    # --- B. Save Match Record ---
    match_doc = match.dict()
    match_doc["calculated_team_result"] = team_result
    await matches_collection.insert_one(match_doc)

    # --- C. Update TEAM Stats ---
    # Update the team's total points, wins, losses in the 'teams' collection
    team_update_fields = {
        "stats.matches_played": 1,
        "stats.points": points_awarded
    }
    if team_result == "win": team_update_fields["stats.wins"] = 1
    elif team_result == "loss": team_update_fields["stats.loss"] = 1
    else: team_update_fields["stats.draws"] = 1

    await teams_collection.update_one(
        {"_id": ObjectId(match.team_id)},
        {"$inc": team_update_fields}
    )

    # --- D. Update PLAYER Stats (Global & Tournament) ---
    for p_res in match.player_results:
        pid = p_res.player_id
        res = p_res.result

        if res == "sub":
            continue

        # 1. Update Global Career Stats (In 'players' collection)
        global_inc = {"matches_played": 1}
        if res == "win": global_inc["wins"] = 1
        elif res == "loss": global_inc["loss"] = 1
        elif res == "draw": global_inc["draws"] = 1
        
        await collection.update_one({"_id": ObjectId(pid)}, {"$inc": global_inc})

        # 2. Update Tournament-Specific Stats (In 'teams' collection -> players array)
        tourney_inc = {"players.$.stats.matches_played": 1}
        if res == "win": tourney_inc["players.$.stats.wins"] = 1
        elif res == "loss": tourney_inc["players.$.stats.loss"] = 1
        elif res == "draw": tourney_inc["players.$.stats.draws"] = 1

        await teams_collection.update_one(
            {"_id": ObjectId(match.team_id), "players.player_id": pid},
            {"$inc": tourney_inc}
        )

    return {
        "message": "Match recorded successfully",
        "team_result": team_result,
        "points_awarded": points_awarded
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)