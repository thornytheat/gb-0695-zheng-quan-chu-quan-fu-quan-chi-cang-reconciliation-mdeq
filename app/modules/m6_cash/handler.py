from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m6',__name__)
@bp.route('/api/m6', methods=['GET','POST'])
def module_6(): return jsonify(stage(6))
