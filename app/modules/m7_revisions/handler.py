from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m7',__name__)
@bp.route('/api/m7', methods=['GET','POST'])
def module_7(): return jsonify(stage(7))
