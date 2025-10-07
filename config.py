import os

class Config:
    SECRET_KEY = "super-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///sasyanova.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
