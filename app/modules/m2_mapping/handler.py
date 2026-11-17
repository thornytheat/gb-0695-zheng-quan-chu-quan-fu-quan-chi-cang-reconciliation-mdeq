from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m2',__name__)
@bp.route('/api/m2', methods=['GET','POST'])
def module_2(): return jsonify(stage(2))
