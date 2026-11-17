from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m1',__name__)
@bp.route('/api/m1', methods=['GET','POST'])
def module_1(): return jsonify(stage(1))
